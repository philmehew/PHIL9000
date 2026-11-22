"""Home Assistant plumbing for the optional LLM analyser and activity feed.

Reads observation history from HA's recorder, asks the household's
Assist/conversation agent to refine candidate patterns and propose new ones,
and ingests the schema-validated output (see analyser.py). Nothing here ever
acts: proposals are inert until the user confirms them. The recorder and
agent are injected-able for deterministic tests.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .activity import DEFAULT_LIMIT, dedupe_activity
from .analyser import (
    DEFAULT_LOOKBACK_DAYS,
    build_prompt,
    parse_analysis_reply,
    summarise_history,
)
from .const import CONF_ENTITIES, CONF_EXCLUDE_ENTITIES, OPTION_AGENT_ID, OPTION_ANALYSIS_DAYS
from .notifier import async_notify
from .subscriptions import Subscriptions, registry_is_noise

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
            events_by_entity[entity_id] = [(state.last_changed, state.state) for state in states]
    return events_by_entity


ACTIVITY_LOOKBACK_DAYS = 7


async def async_fetch_activity(
    hass: HomeAssistant, entity_ids: list[str], limit: int = DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """Newest real state changes on the observed entities (the panel feed).

    Reads the recorder — HA's single central activity log — per entity, marks
    attribute-only rows as non-changes, derives old_state from the previous
    recorded state, and merges through dedupe_activity (newest first, capped).
    """
    from homeassistant.components.recorder import (  # function-local: purity guard
        history,
    )

    if "recorder" not in hass.config.components:
        raise AnalysisUnavailableError(
            "the recorder/history integration is not available, so PHIL9000 has no activity to show"
        )
    start = datetime.now(UTC) - timedelta(days=ACTIVITY_LOOKBACK_DAYS)
    rows: list[dict[str, Any]] = []
    for entity_id in entity_ids:
        try:
            changes = await hass.async_add_executor_job(
                history.state_changes_during_period,
                hass,
                start,
                None,
                entity_id,
                True,  # no_attributes
                True,  # descending: newest first
                limit,
                False,  # include_start_time_state
            )
        except Exception:  # noqa: BLE001 — one bad entity must not sink the feed
            _LOGGER.debug("Could not read activity for %s", entity_id, exc_info=True)
            continue
        states = changes.get(entity_id, [])
        for index, state in enumerate(states):
            older = states[index + 1] if index + 1 < len(states) else None
            rows.append(
                {
                    "entity_id": entity_id,
                    "at": state.last_changed.astimezone(UTC).isoformat(),
                    "old_state": older.state if older is not None else None,
                    "new_state": state.state,
                    # Attribute-only rows keep the previous last_changed.
                    "state_changed": state.last_changed == state.last_updated,
                }
            )
    return dedupe_activity(rows, limit)


def _dig(data: object, *keys: str) -> Any:
    """Walk nested JSON values, yielding None when a key path doesn't hold."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


async def async_call_agent(hass: HomeAssistant, agent_id: str | None, prompt: str) -> str:
    """Ask the household's conversation agent (no API keys of our own)."""
    payload: dict[str, Any] = {"text": prompt}
    if agent_id:
        payload["agent_id"] = agent_id
    response = await hass.services.async_call(
        "conversation", "process", payload, blocking=True, return_response=True
    )
    # response shape: {"response": {"speech": {"plain": {"speech": "..."}}}}
    return str(_dig(response, "response", "speech", "plain", "speech") or "")


def _expand_specs(hass: HomeAssistant, specs: list[str], exclude: list[str]) -> list[str]:
    """Resolve domain/entity-id subscriptions to concrete entity ids."""
    from homeassistant.helpers.entity_registry import (  # function-local: purity guard
        async_get as async_get_entity_registry,
    )

    is_noise = registry_is_noise(async_get_entity_registry(hass))
    return Subscriptions(specs, exclude).expand(hass.states.async_entity_ids(), is_noise)


async def async_run_analysis(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: Phil9000Runtime,
    *,
    history_provider: HistoryProvider | None = None,
    agent_call: AgentCall | None = None,
    notify: bool = True,
) -> dict[str, list[str]]:
    """One analysis pass: history → prompt → agent → refined + new candidates.

    Candidates are refined in place (the engine keeps their ids, counters and
    any user-set name); new patterns enter as candidates. Confirmed and
    dismissed behaviours are never touched (enforced by the parser). Returns
    a summary ``{"refined": [names], "new": [names]}``; raises
    AnalysisUnavailableError when the run cannot proceed.
    """
    if runtime.analysis_running:
        raise AnalysisUnavailableError("a PHIL9000 analysis run is already in progress")
    runtime.analysis_running = True
    try:
        return await _async_analysis_pass(
            hass, entry, runtime, history_provider, agent_call, notify
        )
    finally:
        runtime.analysis_running = False


async def _async_analysis_pass(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: Phil9000Runtime,
    history_provider: HistoryProvider | None,
    agent_call: AgentCall | None,
    notify: bool,
) -> dict[str, list[str]]:
    specs = list(entry.data.get(CONF_ENTITIES, []))
    exclude = list(entry.data.get(CONF_EXCLUDE_ENTITIES, []))
    days = int(entry.options.get(OPTION_ANALYSIS_DAYS, DEFAULT_LOOKBACK_DAYS))
    provider = history_provider or (
        lambda: async_collect_history(hass, _expand_specs(hass, specs, exclude), days)
    )
    call = agent_call or async_call_agent

    events_by_entity = await provider()
    if not any(events_by_entity.values()):
        raise AnalysisUnavailableError("no recorded history for the observed entities yet")

    existing = list(runtime.engine.behaviours.values())
    prompt = build_prompt(summarise_history(events_by_entity), existing)
    try:
        reply = await call(hass, entry.options.get(OPTION_AGENT_ID), prompt)
    except Exception as err:  # noqa: BLE001 — surface as a friendly service error
        raise AnalysisUnavailableError(f"conversation agent call failed: {err}") from err

    result = parse_analysis_reply(reply, existing)
    if result.refined or result.new:
        for behaviour in result.new:
            runtime.engine.add_behaviour(behaviour)
        await hass.async_add_executor_job(
            runtime.store.save, list(runtime.engine.behaviours.values())
        )

    parts: list[str] = []
    if result.refined:
        parts.append(f"Refined {len(result.refined)} candidate(s): {_names(result.refined)}")
    if result.new:
        parts.append(f"Proposed {len(result.new)} new candidate behaviour(s): {_names(result.new)}")
    message = " · ".join(parts) if parts else "No new convincing patterns this time."
    _LOGGER.info("PHIL9000 analysis: %s", message)
    if notify:
        await async_notify(hass, entry, "PHIL9000 pattern analysis", message)
    return {
        "refined": [b.name for b in result.refined],
        "new": [b.name for b in result.new],
    }


def _names(behaviours: list[Any]) -> str:
    return ", ".join(b.name for b in behaviours)
