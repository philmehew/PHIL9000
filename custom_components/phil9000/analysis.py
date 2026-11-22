"""Home Assistant plumbing for the optional LLM analyser (Milestone 5).

Reads observation history from HA's recorder, asks the household's
Assist/conversation agent to propose recurring patterns, and ingests the
schema-validated output (see analyser.py) as CANDIDATE behaviours. Nothing
here ever acts: proposals are inert until the user confirms them. The
recorder and agent are injected-able for deterministic tests.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .analyser import (
    DEFAULT_LOOKBACK_DAYS,
    build_prompt,
    parse_proposals,
    summarise_history,
)
from .const import CONF_ENTITIES, OPTION_AGENT_ID, OPTION_ANALYSIS_DAYS
from .notifier import async_notify

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from . import Phil9000Runtime

_LOGGER = logging.getLogger(__name__)

# Injectable seams for tests: history provider and agent call.
HistoryProvider = Callable[[], Awaitable[dict[str, list[tuple[datetime, str]]]]]
AgentCall = Callable[[Any, "str | None", str], Awaitable[str]]


class AnalysisUnavailableError(Exception):
    """Raised when a run cannot proceed (no recorder, no history, agent error)."""


async def async_collect_history(
    hass: HomeAssistant, entity_ids: list[str], days: int
) -> dict[str, list[tuple[datetime, str]]]:
    """State-change history per entity, read from HA's recorder."""
    from homeassistant.components.recorder import (  # function-local: purity guard
        history,
    )

    if "recorder" not in hass.config.components:
        raise AnalysisUnavailableError(
            "the recorder/history integration is not available, so PHIL9000 has "
            "no history to analyse"
        )
    start = datetime.now(UTC) - timedelta(days=days)
    events_by_entity: dict[str, list[tuple[datetime, str]]] = {}
    for entity_id in entity_ids:
        try:
            changes = await hass.async_add_executor_job(
                history.state_changes_during_period,
                hass,
                start,
                None,
                entity_id,
                True,  # no_attributes
                False,  # descending
                None,  # limit
                False,  # include_start_time_state
            )
        except Exception:  # noqa: BLE001 — one bad entity must not sink the run
            _LOGGER.debug("Could not read history for %s", entity_id, exc_info=True)
            continue
        states = changes.get(entity_id, [])
        if states:
            events_by_entity[entity_id] = [
                (state.last_changed, state.state) for state in states
            ]
    return events_by_entity


async def async_call_agent(
    hass: HomeAssistant, agent_id: str | None, prompt: str
) -> str:
    """Ask the household's conversation agent (no API keys of our own)."""
    payload: dict[str, Any] = {"text": prompt}
    if agent_id:
        payload["agent_id"] = agent_id
    response = await hass.services.async_call(
        "conversation", "process", payload, blocking=True, return_response=True
    )
    speech = ((response or {}).get("response") or {}).get("speech") or {}
    plain = speech.get("plain") or {}
    return str(plain.get("speech", ""))


async def async_run_analysis(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: Phil9000Runtime,
    *,
    history_provider: HistoryProvider | None = None,
    agent_call: AgentCall | None = None,
    notify: bool = True,
) -> int:
    """One analysis pass: history → prompt → agent → validated candidates.

    Returns the number of new candidate behaviours added; raises
    AnalysisUnavailableError when the run cannot proceed.
    """
    entities = list(entry.data.get(CONF_ENTITIES, []))
    days = int(entry.options.get(OPTION_ANALYSIS_DAYS, DEFAULT_LOOKBACK_DAYS))
    provider = history_provider or (
        lambda: async_collect_history(hass, entities, days)
    )
    call = agent_call or async_call_agent

    events_by_entity = await provider()
    if not any(events_by_entity.values()):
        raise AnalysisUnavailableError(
            "no recorded history for the observed entities yet"
        )

    existing = list(runtime.engine.behaviours.values())
    prompt = build_prompt(summarise_history(events_by_entity), existing)
    try:
        reply = await call(hass, entry.options.get(OPTION_AGENT_ID), prompt)
    except Exception as err:  # noqa: BLE001 — surface as a friendly service error
        raise AnalysisUnavailableError(f"conversation agent call failed: {err}") from err

    proposals = parse_proposals(reply, existing)
    for behaviour in proposals:
        runtime.engine.add_behaviour(behaviour)
    if proposals:
        await hass.async_add_executor_job(
            runtime.store.save, list(runtime.engine.behaviours.values())
        )

    names = ", ".join(b.name for b in proposals)
    message = (
        f"Proposed {len(proposals)} new candidate behaviour(s): {names}. "
        "Review them in the PHIL9000 panel — nothing acts until you confirm."
        if proposals
        else "No new convincing patterns this time."
    )
    _LOGGER.info("PHIL9000 analysis: %s", message)
    if notify:
        await async_notify(hass, entry, "PHIL9000 pattern analysis", message)
    return len(proposals)
