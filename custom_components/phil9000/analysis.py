"""Home Assistant plumbing for the optional LLM analyser and activity feed.

Reads observation history from HA's recorder, asks the household's
Assist/conversation agent to refine candidate patterns and propose new ones,
and ingests the schema-validated output (see analyser.py). Nothing here ever
acts: proposals are inert until the user confirms them. The recorder and
agent are injected-able for deterministic tests.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .activity import DEFAULT_LIMIT, dedupe_activity, ring_history
from .analyser import (
    DEFAULT_LOOKBACK_DAYS,
    build_prompt,
    extract_reply_json,
    parse_analysis_reply,
    summarise_history,
)
from .const import (
    ANALYSIS_LOG_LIMIT,
    CONF_ENTITIES,
    CONF_EXCLUDE_ENTITIES,
    OPTION_AGENT_ID,
    OPTION_ANALYSIS_DAYS,
    OPTION_CUSTOM_PROMPT,
)
from .notifier import async_notify
from .recording import build_recording_prompt, parse_automation_reply
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


# A hung conversation agent must not hold the in-flight guard until restart.
AGENT_TIMEOUT_SECONDS = 600


async def async_collect_history(
    hass: HomeAssistant, entity_ids: list[str], days: int
) -> dict[str, list[tuple[datetime, str]]]:
    """State-change history per entity, read from HA's recorder.

    One recorder query for the whole entity list: whole-domain subscriptions
    can mean hundreds of entities, and a query per entity meant hundreds of
    database round-trips per analysis run. (``get_significant_states`` is the
    batch API — ``state_changes_during_period`` takes a single entity id.)
    """
    from homeassistant.components.recorder import (  # function-local: purity guard
        history,
    )

    if "recorder" not in hass.config.components:
        raise AnalysisUnavailableError(
            "the recorder/history integration is not available, so PHIL9000 has "
            "no history to analyse"
        )
    if not entity_ids:
        return {}
    start = datetime.now(UTC) - timedelta(days=days)
    try:
        changes = await hass.async_add_executor_job(
            history.get_significant_states,
            hass,
            start,
            None,
            entity_ids,
            None,  # filters
            False,  # include_start_time_state
            True,  # significant_changes_only (drops attribute-only rows)
            False,  # minimal_response
            True,  # no_attributes
        )
    except Exception as err:  # noqa: BLE001 — surface as a friendly error
        _LOGGER.warning("Could not read history for the analysis", exc_info=True)
        raise AnalysisUnavailableError(f"reading history failed: {err}") from err
    events_by_entity: dict[str, list[tuple[datetime, str]]] = {}
    for entity_id in entity_ids:
        # minimal_response/compressed_state_format are off: rows are States.
        states = changes.get(entity_id, [])
        if states:
            events_by_entity[entity_id] = [
                (state.last_changed, state.state)  # type: ignore[union-attr]
                for state in states
            ]
    return events_by_entity


# Seven days back, capped at DEFAULT_LIMIT rows after filtering: with the
# noise rules in place a busy home rarely reaches 1000 real changes in a
# shorter window, so the cap — not the window — is the usual bound. The
# query is one indexed read (significant changes only, per metadata_id)
# when the panel opens; the row cap still bounds the websocket payload.
ACTIVITY_LOOKBACK_HOURS = 168


async def async_fetch_activity(
    hass: HomeAssistant, entity_ids: list[str], limit: int = DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """Newest real state changes on the observed entities (the panel feed).

    Reads the recorder — HA's single central activity log — in one query,
    marks attribute-only rows as non-changes, derives old_state from the
    previous recorded state, and merges through dedupe_activity (newest
    first, capped). The window is a week but the cap usually binds first
    (with availability/no-change rows filtered, even busy homes rarely
    produce 1000 real changes faster than that); the query stays a single
    indexed read, and the cap bounds the websocket payload.
    """
    from homeassistant.components.recorder import (  # function-local: purity guard
        history,
    )

    if "recorder" not in hass.config.components:
        raise AnalysisUnavailableError(
            "the recorder/history integration is not available, so PHIL9000 has no activity to show"
        )
    if not entity_ids:
        return []
    start = datetime.now(UTC) - timedelta(hours=ACTIVITY_LOOKBACK_HOURS)
    try:
        # get_significant_states: the batch API (a list of entity ids in one
        # query). It returns rows ascending (oldest first) and excludes
        # attribute-only changes by default.
        changes = await hass.async_add_executor_job(
            history.get_significant_states,
            hass,
            start,
            None,
            entity_ids,
            None,  # filters
            False,  # include_start_time_state
            True,  # significant_changes_only (drops attribute-only rows)
            False,  # minimal_response
            True,  # no_attributes
        )
    except Exception as err:  # noqa: BLE001 — the panel must see the reason
        _LOGGER.warning("Could not read activity for the feed", exc_info=True)
        raise AnalysisUnavailableError(f"reading activity failed: {err}") from err
    rows: list[dict[str, Any]] = []
    for entity_id in entity_ids:
        # minimal_response/compressed_state_format are off: rows are States.
        states = changes.get(entity_id, [])
        for index, state in enumerate(states):
            older = states[index - 1] if index > 0 else None
            rows.append(
                {
                    "entity_id": entity_id,
                    "at": state.last_changed.astimezone(UTC).isoformat(),  # type: ignore[union-attr]
                    "old_state": older.state if older is not None else None,  # type: ignore[union-attr]
                    "new_state": state.state,  # type: ignore[union-attr]
                    # The query already excludes attribute-only rows.
                    "state_changed": True,
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
    """Ask the household's LLM agent (no API keys of our own).

    Two routes, picked by the configured agent's domain:

    - ``ai_task.*`` — HA's purpose-built structured-data route. The reply
      is validated against ANALYSIS_REPLY_SCHEMA by Home Assistant before
      PHIL9000 sees it, and no voice-assistant system prompt, tool set or
      word limit stands between the analysis and its JSON (a real install
      spent months silently losing results to a conversation agent whose
      instructions demanded one-sentence plain-text replies).
    - ``conversation.*`` (or blank) — the household conversation agent.

    Bounded: a hung agent call would otherwise hold the in-flight guard
    (and block every later run) until Home Assistant restarts.
    """
    if agent_id and agent_id.startswith("ai_task."):
        return await _async_call_ai_task_agent(hass, agent_id, prompt)
    payload: dict[str, Any] = {"text": prompt}
    if agent_id:
        payload["agent_id"] = agent_id
    try:
        async with asyncio.timeout(AGENT_TIMEOUT_SECONDS):
            response = await hass.services.async_call(
                "conversation", "process", payload, blocking=True, return_response=True
            )
    except TimeoutError:
        raise AnalysisUnavailableError(
            f"conversation agent call timed out after {AGENT_TIMEOUT_SECONDS // 60} minutes"
        ) from None
    # response shape: {"response": {"speech": {"plain": {"speech": "..."}}}}
    return str(_dig(response, "response", "speech", "plain", "speech") or "")


async def _async_call_ai_task_agent(hass: HomeAssistant, agent_id: str, prompt: str) -> str:
    """Run the analysis as an ai_task data-generation task."""
    import json

    from homeassistant.components import ai_task  # function-local: purity guard

    try:
        async with asyncio.timeout(AGENT_TIMEOUT_SECONDS):
            result = await ai_task.async_generate_data(
                hass,
                task_name="PHIL9000 pattern analysis",
                entity_id=agent_id,
                instructions=prompt,
                # No "structure": HA 2026.11's merged LLM custom serializer
                # (helpers/llm.py) forwards probatio's UNSUPPORTED sentinel
                # into to_openapi for JSON-Schema-derived nodes, which dies
                # with "'_Unsupported' object is not subscriptable" before
                # any request is sent. PHIL9000's reply parser enforces the
                # same contract (ANALYSIS_REPLY_SCHEMA documents it), so the
                # schema is validation we can re-add once HA fixes the
                # sentinel handling.
            )
    except TimeoutError:
        raise AnalysisUnavailableError(
            f"AI task agent call timed out after {AGENT_TIMEOUT_SECONDS // 60} minutes"
        ) from None
    except AnalysisUnavailableError:
        raise
    except Exception as err:  # noqa: BLE001 — surface as a friendly service error
        raise AnalysisUnavailableError(f"AI task agent call failed: {err}") from err
    data = result.data
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return json.dumps(data)
    raise AnalysisUnavailableError(
        f"the AI task agent returned {type(data).__name__} instead of text or structured data — "
        "check the agent's model and max-tokens settings"
    )


def _expand_specs(hass: HomeAssistant, specs: list[str], exclude: list[str]) -> list[str]:
    """Resolve domain/entity-id subscriptions to concrete entity ids."""
    from homeassistant.helpers.entity_registry import (  # function-local: purity guard
        async_get as async_get_entity_registry,
    )

    is_noise = registry_is_noise(async_get_entity_registry(hass))
    return Subscriptions(specs, exclude).expand(hass.states.async_entity_ids(), is_noise)


def _log_run(runtime: Phil9000Runtime, level: str, message: str) -> None:
    """Record one analysis-log line: system log, panel ring buffer, live push.

    Entries newest-last, capped at ANALYSIS_LOG_LIMIT. The file write happens
    once per run (see async_run_analysis) — a run is rare and short, and a
    single awaited save avoids racing executor writes over the file.

    Each entry carries ``running`` — whether a run is still in flight after
    this event — so the panel can show/hide its "Analysis running…" status
    from the live pushes alone.
    """
    line = "PHIL9000 analysis: %s"
    (_LOGGER.error if level == "error" else _LOGGER.info)(line, message)
    entry = {
        "at": datetime.now(UTC).isoformat(),
        "level": level,
        "message": message,
        "running": runtime.analysis_running,
    }
    runtime.analysis_log.append(entry)
    del runtime.analysis_log[:-ANALYSIS_LOG_LIMIT]
    for subscriber in list(runtime.analysis_log_subscribers):
        subscriber(dict(entry))


async def _async_persist_log(hass: HomeAssistant, runtime: Phil9000Runtime) -> None:
    """Write the analysis log file; a log write must never sink a run."""
    if runtime.analysis_log_store is None:
        return
    try:
        await hass.async_add_executor_job(
            runtime.analysis_log_store.save, list(runtime.analysis_log)
        )
    except OSError:
        _LOGGER.warning("PHIL9000 could not persist the analysis log", exc_info=True)


async def async_run_analysis(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: Phil9000Runtime,
    *,
    history_provider: HistoryProvider | None = None,
    agent_call: AgentCall | None = None,
    notify: bool = True,
    trigger: str = "manual",
) -> dict[str, list[str]]:
    """One analysis pass: history → prompt → agent → refined + new candidates.

    Candidates are refined in place (the engine keeps their ids, counters and
    any user-set name); new patterns enter as candidates. Confirmed and
    dismissed behaviours are never touched (enforced by the parser). Returns
    a summary ``{"refined": [names], "new": [names]}``; raises
    AnalysisUnavailableError when the run cannot proceed. Each outcome is
    also recorded in the panel's analysis log (``trigger`` says who started
    it: "manual", "service" or "nightly").
    """
    if runtime.analysis_running:
        _log_run(runtime, "error", "Skipped: another analysis run is already in progress")
        await _async_persist_log(hass, runtime)  # the run path never saves for us
        raise AnalysisUnavailableError("a PHIL9000 analysis run is already in progress")
    runtime.analysis_running = True
    started = time.monotonic()
    _log_run(runtime, "info", f"Run started ({trigger})")
    try:
        try:
            summary = await _async_analysis_pass(
                hass, entry, runtime, history_provider, agent_call, notify
            )
        except AnalysisUnavailableError as err:
            # Terminal lines log the post-event state, so the "Finished"/"Run
            # failed" entries carry running=False (the finally below re-clears
            # idempotently).
            runtime.analysis_running = False
            _log_run(runtime, "error", f"Run failed: {err}")
            raise
        except Exception as err:  # noqa: BLE001 — log, then let HA surface it
            runtime.analysis_running = False
            _log_run(runtime, "error", f"Run failed: {type(err).__name__}: {err}")
            raise
        details: list[str] = []
        if summary["refined"]:
            details.append(f"refined {', '.join(summary['refined'])}")
        if summary["new"]:
            details.append(f"proposed {', '.join(summary['new'])}")
        outcome = "; ".join(details) if details else "no new convincing patterns"
        runtime.analysis_running = False
        _log_run(runtime, "info", f"Finished in {time.monotonic() - started:.0f}s — {outcome}")
        return summary
    finally:
        runtime.analysis_running = False
        await _async_persist_log(hass, runtime)


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
    # Button/remote presses never reach the states recorder, so the ring is
    # their only history; fold it in and the model sees "the user pressed X"
    # like any other signal. Ring rows *replace* recorder rows for the same
    # entities (the recorder under-reports repeat presses — see activity.py).
    presses = ring_history(
        runtime.event_rows,
        datetime.now(UTC) - timedelta(days=days),
        lambda entity_id: runtime.subscriptions.matches(entity_id, runtime.is_noise),
    )
    for entity_id, rows in presses.items():
        events_by_entity[entity_id] = sorted(rows)
    if not any(events_by_entity.values()):
        raise AnalysisUnavailableError("no recorded history for the observed entities yet")

    existing = list(runtime.engine.behaviours.values())
    total_events = sum(len(rows) for rows in events_by_entity.values())
    live_entities = sum(1 for rows in events_by_entity.values() if rows)
    known = "behaviour" if len(existing) == 1 else "behaviours"
    _log_run(
        runtime,
        "info",
        f"Collected {total_events} state changes from {live_entities} entities"
        + (
            f" (incl. {sum(len(rows) for rows in presses.values())} button presses)"
            if presses
            else ""
        )
        + f", {days}-day window, {len(existing)} known {known}",
    )
    prompt = build_prompt(
        summarise_history(events_by_entity),
        existing,
        (entry.options.get(OPTION_CUSTOM_PROMPT) or "").strip() or None,
    )
    agent_id = entry.options.get(OPTION_AGENT_ID)
    _log_run(
        runtime,
        "info",
        f"Sending the summary ({len(prompt) // 1000}k chars) to "
        f"{agent_id or 'the default conversation agent'} — "
        "LLM analysis can take a few minutes; results appear here when it replies",
    )
    try:
        reply = await call(hass, agent_id, prompt)
    except Exception as err:  # noqa: BLE001 — surface as a friendly service error
        raise AnalysisUnavailableError(f"conversation agent call failed: {err}") from err

    # A reply that isn't JSON at all is an agent problem (wrong model,
    # error speech, a reasoning model whose content was starved by a small
    # max-tokens) — reporting it as "no new convincing patterns" hides the
    # misconfiguration, so fail the run with the evidence instead.
    if not reply.strip():
        raise AnalysisUnavailableError(
            "conversation agent returned an empty reply — check the agent's "
            "model and max-tokens settings (a reasoning model can spend its "
            "whole output budget on hidden thinking and leave no content)"
        )
    if extract_reply_json(reply) is None:
        head = " ".join(reply.split())[:160]
        raise AnalysisUnavailableError(
            f"the conversation agent's reply was not JSON (starts: “{head}”) — "
            "check the agent's model and max-tokens settings"
        )

    result = parse_analysis_reply(reply, existing)
    # Guardrail drops are visible: a run that silently eats everything the
    # model proposed is indistinguishable from a model that proposed nothing.
    for name, reason in result.dropped:
        _log_run(runtime, "info", f"Dropped candidate '{name}': {reason}")
    if not result.refined and not result.new:
        head = " ".join(reply.split())[:150]
        _log_run(runtime, "info", f"Empty result — reply head: “{head}”")
    if result.refined or result.new:
        for behaviour in result.new:
            runtime.engine.add_behaviour(behaviour)
        # Refinements rewrite steps of existing behaviours (possibly onto
        # new entities), so the engine's step-entity index must be rebuilt.
        runtime.engine.rebuild_index()
        await hass.async_add_executor_job(
            runtime.store.save, list(runtime.engine.behaviours.values())
        )

    parts: list[str] = []
    if result.refined:
        parts.append(f"Refined {len(result.refined)} candidate(s): {_names(result.refined)}")
    if result.new:
        parts.append(f"Proposed {len(result.new)} new candidate behaviour(s): {_names(result.new)}")
    message = " · ".join(parts) if parts else "No new convincing patterns this time."
    if notify:
        await async_notify(hass, entry, "PHIL9000 pattern analysis", message)
    return {
        "refined": [b.name for b in result.refined],
        "new": [b.name for b in result.new],
    }


def _names(behaviours: list[Any]) -> str:
    return ", ".join(b.name for b in behaviours)


# -- recording proposal (the Record flow's LLM step) --------------------------


async def async_run_recording_pass(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: Phil9000Runtime,
    *,
    message: str | None = None,
    agent_call: AgentCall | None = None,
) -> dict[str, Any]:
    """One LLM turn over the recorded demonstration.

    ``message=None`` asks for a first proposal from the recorded triggers,
    actions and notes; a message refines the existing proposal (the panel's
    chat box). The validated proposal lands on the session, ready for
    ``record_save``. Reuses the analysis single-flight guard and analysis log,
    so proposal progress and guardrail drops are visible like nightly runs.
    """
    session = runtime.recording
    if runtime.analysis_running:
        raise AnalysisUnavailableError("a PHIL9000 LLM run is already in progress")
    if not session.triggers:
        raise AnalysisUnavailableError("record at least one trigger before asking for a proposal")
    if not session.actions and not session.notes.strip():
        raise AnalysisUnavailableError(
            "record an action (or add notes describing one) before asking for a proposal"
        )
    if message is not None and session.proposal is None:
        raise AnalysisUnavailableError("no proposal to refine yet — press Propose first")

    runtime.analysis_running = True
    started = time.monotonic()
    kind = "refinement" if message is not None else "proposal"
    if message is not None:
        session.add_turn("user", message.strip())
    _log_run(runtime, "info", f"Recording {kind} started")
    try:
        prompt = build_recording_prompt(
            session, message=message, known=list(runtime.engine.behaviours.values())
        )
        agent_id = entry.options.get(OPTION_AGENT_ID)
        call = agent_call or async_call_agent
        try:
            reply = await call(hass, agent_id, prompt)
        except AnalysisUnavailableError:
            raise
        except Exception as err:  # noqa: BLE001 — surface as a friendly error
            raise AnalysisUnavailableError(f"agent call failed: {err}") from err
        result = parse_automation_reply(reply, session)
        if result is None:
            head = " ".join(reply.split())[:160]
            raise AnalysisUnavailableError(
                f"the agent reply was not JSON (starts: “{head}”) — check the agent's "
                "model and max-tokens settings"
            )
        for what, why in result.dropped:
            _log_run(runtime, "info", f"Recording {kind}: dropped {what}: {why}")
        for warning in result.warnings:
            _log_run(runtime, "info", f"Recording {kind}: {warning}")
        if result.proposal is None:
            raise AnalysisUnavailableError(
                "the proposal used none of the recorded triggers — press Propose again"
            )
        session.proposal = result.proposal
        session.add_turn("assistant", result.proposal.get("reply") or "Proposal updated.")
        runtime.analysis_running = False
        _log_run(
            runtime,
            "info",
            f"Recording {kind} finished in {time.monotonic() - started:.0f}s — "
            f"{result.proposal['name']}",
        )
        return dict(result.proposal)
    except AnalysisUnavailableError as err:
        runtime.analysis_running = False
        _log_run(runtime, "error", f"Recording {kind} failed: {err}")
        raise
    except Exception as err:  # noqa: BLE001 — log, then let the caller surface it
        runtime.analysis_running = False
        _log_run(runtime, "error", f"Recording {kind} failed: {type(err).__name__}: {err}")
        raise
    finally:
        runtime.analysis_running = False
        await _async_persist_log(hass, runtime)
        await runtime.async_persist_recording(hass)
