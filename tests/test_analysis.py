"""HA-harness tests for the analyser plumbing (Milestone 5), mocked end to end.

The recorder read and the conversation agent are injected, so these tests
are deterministic: fake history in, fake LLM JSON out, validated candidates
on disk.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from homeassistant.core import HomeAssistant
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

    added = await async_run_analysis(
        hass, entry, runtime, history_provider=_fake_history, agent_call=agent
    )
    await hass.async_block_till_done()

    assert added == 1
    assert agent.calls[0][0] is None  # no agent configured -> default
    assert "binary_sensor.office_presence" in agent.calls[0][1]

    reloaded = reload_behaviours(hass)
    proposed = [b for b in reloaded if b.name == "Evening handoff"]
    assert len(proposed) == 1
    assert proposed[0].status.value == "candidate"
    assert proposed[0].actions == []

    assert len(notifies) == 1
    assert "1 new candidate" in notifies[0].data["message"]

    # Second run with the same answer: deterministic ids deduplicate.
    again = await async_run_analysis(
        hass, entry, runtime, history_provider=_fake_history, agent_call=agent
    )
    assert again == 0


async def test_run_analysis_survives_unparseable_llm_output(
    hass: HomeAssistant,
) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]

    babbling = FakeAgent("I'm not sure, maybe something with the kitchen?")
    added = await async_run_analysis(
        hass, entry, runtime, history_provider=_fake_history, agent_call=babbling
    )
    assert added == 0
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
