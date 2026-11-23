"""HA-harness tests for the analyser plumbing (Milestone 5), mocked end to end.

The recorder read and the conversation agent are injected, so these tests
are deterministic: fake history in, fake LLM JSON out, validated candidates
on disk.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import ServiceValidationError

from custom_components.phil9000.analysis import (
    AnalysisUnavailableError,
    async_run_analysis,
)
from custom_components.phil9000.const import (
    DOMAIN,
    OPTION_ANALYSIS_ENABLED,
    OPTION_NOTIFY_ENTITY,
    SERVICE_RUN_ANALYSIS,
)
from custom_components.phil9000.models import BehaviourStatus
from custom_components.phil9000.seed import end_of_work_day
from tests.helpers import (
    KITCHEN,
    OFFICE,
    capture_calls,
    reload_behaviours,
    seed_behaviours,
    setup_entry,
)

pytest.importorskip("pytest_homeassistant_custom_component")

PROPOSAL = {
    "name": "Evening handoff",
    "description": "Office off then kitchen on in the evening.",
    "steps": [
        {"entity_id": OFFICE, "to_state": "off"},
        {"entity_id": KITCHEN, "to_state": "on", "max_gap_seconds": 240},
    ],
}
LLM_REPLY = f"```json\n{json.dumps([PROPOSAL])}\n```"


@pytest.fixture(autouse=True)
def auto_enable_custom(enable_custom_integrations):  # noqa: ANN001, ARG001
    """Enable custom_components loading for every test in this module."""


async def _fake_history():
    day = datetime(2026, 11, 23, tzinfo=UTC)
    return {
        OFFICE: [(day.replace(hour=8), "on"), (day.replace(hour=17, minute=30), "off")],
        KITCHEN: [(day.replace(hour=17, minute=32), "on")],
    }


class FakeAgent:
    """Conversation-agent double that records (agent_id, prompt) calls."""

    def __init__(self, reply: str = LLM_REPLY) -> None:
        self.reply = reply
        self.calls: list[tuple[str | None, str]] = []

    async def __call__(self, hass, agent_id, prompt):  # noqa: ANN001
        self.calls.append((agent_id, prompt))
        return self.reply


async def test_run_analysis_adds_and_persists_candidates(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    notifies = capture_calls(hass, "notify", "send_message")
    entry = await setup_entry(hass, options={OPTION_NOTIFY_ENTITY: "notify.mobile_phone"})
    runtime = hass.data[DOMAIN][entry.entry_id]
    agent = FakeAgent()

    summary = await async_run_analysis(
        hass, entry, runtime, history_provider=_fake_history, agent_call=agent
    )
    await hass.async_block_till_done()

    assert summary == {"refined": [], "new": ["Evening handoff"]}
    assert agent.calls[0][0] is None  # no agent configured -> default
    assert "binary_sensor.office_presence" in agent.calls[0][1]

    reloaded = reload_behaviours(hass)
    proposed = [b for b in reloaded if b.name == "Evening handoff"]
    assert len(proposed) == 1
    assert proposed[0].status.value == "candidate"
    assert proposed[0].actions == []

    assert len(notifies) == 1
    assert "Proposed 1 new candidate behaviour(s)" in notifies[0].data["message"]

    # Second run with the same answer: deterministic ids deduplicate.
    again = await async_run_analysis(
        hass, entry, runtime, history_provider=_fake_history, agent_call=agent
    )
    assert again == {"refined": [], "new": []}


async def test_run_analysis_refines_candidate_and_persists(hass: HomeAssistant) -> None:
    candidate = end_of_work_day(status=BehaviourStatus.CANDIDATE)
    candidate.occurrences = 2
    seed_behaviours(hass, candidate)
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]

    update = {
        "id": "end_of_work_day",
        "name": "Work's done",
        "description": "Consistently 17:30, gaps under three minutes.",
        "steps": [
            {"entity_id": OFFICE, "to_state": "off"},
            {"entity_id": KITCHEN, "to_state": "on", "max_gap_seconds": 180},
        ],
        "weekdays": [1, 2, 3, 4, 5],
        "time_start": "17:00",
        "time_end": "18:30",
    }
    agent = FakeAgent(json.dumps({"updates": [update], "new": []}))

    summary = await async_run_analysis(
        hass, entry, runtime, history_provider=_fake_history, agent_call=agent
    )

    assert summary == {"refined": ["Work's done"], "new": []}
    reloaded = reload_behaviours(hass)
    assert len(reloaded) == 1
    refined = reloaded[0]
    assert refined.name == "Work's done"
    assert refined.steps[1].max_gap_seconds == 180
    assert refined.status.value == "candidate"
    assert refined.occurrences == 2  # the engine's tally is untouched


async def test_run_analysis_refuses_concurrent_runs(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]
    runtime.analysis_running = True

    with pytest.raises(AnalysisUnavailableError, match="already in progress"):
        await async_run_analysis(
            hass, entry, runtime, history_provider=_fake_history, agent_call=FakeAgent()
        )
    assert runtime.analysis_running is True  # the guard was not clobbered


async def test_in_flight_guard_resets_after_agent_failure(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]

    async def broken_agent(hass_, agent_id, prompt):  # noqa: ANN001
        raise TimeoutError("agent timed out")

    with pytest.raises(AnalysisUnavailableError, match="agent call failed"):
        await async_run_analysis(
            hass, entry, runtime, history_provider=_fake_history, agent_call=broken_agent
        )
    assert runtime.analysis_running is False


async def test_run_analysis_survives_unparseable_llm_output(
    hass: HomeAssistant,
) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]

    babbling = FakeAgent("I'm not sure, maybe something with the kitchen?")
    summary = await async_run_analysis(
        hass, entry, runtime, history_provider=_fake_history, agent_call=babbling
    )
    assert summary == {"refined": [], "new": []}
    assert len(reload_behaviours(hass)) == 1  # store untouched


async def test_run_analysis_service_reports_unavailable_recorder(
    hass: HomeAssistant,
) -> None:
    seed_behaviours(hass, end_of_work_day())
    await setup_entry(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_RUN_ANALYSIS)

    # No recorder in the test harness -> friendly validation error, not a crash.
    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(DOMAIN, SERVICE_RUN_ANALYSIS, {}, blocking=True)
    assert "recorder" in str(excinfo.value)


async def test_analysis_history_provider_error_maps_to_unavailable(
    hass: HomeAssistant,
) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]

    async def empty_history():
        return {}

    with pytest.raises(AnalysisUnavailableError):
        await async_run_analysis(
            hass,
            entry,
            runtime,
            history_provider=empty_history,
            agent_call=FakeAgent(),
        )


async def test_nightly_option_setups_cleanly(hass: HomeAssistant) -> None:
    # Enabling nightly analysis must not error during entry setup (the
    # scheduled job itself fires on real time changes, not in tests).
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass, options={OPTION_ANALYSIS_ENABLED: True})
    assert entry.entry_id in hass.data[DOMAIN]


async def test_agent_call_times_out_and_frees_the_guard(hass: HomeAssistant, monkeypatch) -> None:
    """A hung conversation agent must not hold the in-flight guard forever."""
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]

    async def hang(call):  # noqa: ANN001, ARG001
        await asyncio.sleep(5)

    hass.services.async_register(
        "conversation", "process", hang, supports_response=SupportsResponse.ONLY
    )
    monkeypatch.setattr("custom_components.phil9000.analysis.AGENT_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(AnalysisUnavailableError, match="timed out"):
        await async_run_analysis(hass, entry, runtime, history_provider=_fake_history, notify=False)
    await hass.async_block_till_done()
    assert not runtime.analysis_running  # later runs are not blocked
    assert runtime.analysis_log[-1]["level"] == "error"


# -- the analysis run log (panel's "Analysis log" card) -------------------------


async def test_analysis_log_records_a_readable_run(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]
    runtime.analysis_log.clear()  # the store is persisted across tests by design
    pushes: list[dict] = []
    runtime.analysis_log_subscribers.append(pushes.append)

    await async_run_analysis(
        hass,
        entry,
        runtime,
        history_provider=_fake_history,
        agent_call=FakeAgent(),
        notify=False,
    )
    await hass.async_block_till_done()

    messages = [e["message"] for e in runtime.analysis_log]
    assert messages[0] == "Run started (manual)"
    assert any(m.startswith("Collected 3 state changes from 2 entities") for m in messages)
    assert messages[-1].startswith("Finished in ")
    assert "proposed Evening handoff" in messages[-1]
    assert all(e["level"] == "info" for e in runtime.analysis_log)
    datetime.fromisoformat(runtime.analysis_log[-1]["at"])  # panel-parsable stamp

    # Live push mirrors the buffer (start, progress, finish).
    assert [e["message"] for e in pushes] == messages


async def test_analysis_log_records_failures_as_errors(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]
    runtime.analysis_log.clear()

    async def empty_history():
        return {}

    with pytest.raises(AnalysisUnavailableError):
        await async_run_analysis(
            hass,
            entry,
            runtime,
            history_provider=empty_history,
            agent_call=FakeAgent(),
            notify=False,
            trigger="nightly",
        )

    assert runtime.analysis_log[0]["message"] == "Run started (nightly)"
    last = runtime.analysis_log[-1]
    assert last["level"] == "error"
    assert "no recorded history" in last["message"]
    assert not runtime.analysis_running  # failed runs leave no stale guard


async def test_analysis_log_survives_restart(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]
    runtime.analysis_log.clear()  # known starting state (the store persists by design)
    await async_run_analysis(
        hass,
        entry,
        runtime,
        history_provider=_fake_history,
        agent_call=FakeAgent(),
        notify=False,
    )
    await hass.async_block_till_done()  # the run's log write is awaited in finally
    assert runtime.analysis_log

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    fresh = hass.data[DOMAIN][entry.entry_id]
    assert fresh.analysis_log is not runtime.analysis_log
    assert [e["message"] for e in fresh.analysis_log] == [
        e["message"] for e in runtime.analysis_log
    ]


async def test_analysis_log_buffer_is_capped(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]
    runtime.analysis_log_store = None  # memory only: this tests the ring buffer

    from custom_components.phil9000.analysis import _log_run
    from custom_components.phil9000.const import ANALYSIS_LOG_LIMIT

    for i in range(ANALYSIS_LOG_LIMIT + 10):
        _log_run(runtime, "info", f"line {i}")

    assert len(runtime.analysis_log) == ANALYSIS_LOG_LIMIT
    assert runtime.analysis_log[0]["message"] == "line 10"  # oldest dropped first
    assert runtime.analysis_log[-1]["message"] == f"line {ANALYSIS_LOG_LIMIT + 9}"
