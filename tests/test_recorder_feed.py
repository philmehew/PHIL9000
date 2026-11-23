"""End-to-end feed tests against a real (sqlite) recorder instance.

The recorder fetch path is otherwise untested — the harness normally has no
recorder, so the websocket activity command only ever sees the "unavailable"
error. These tests give PHIL9000 a real recorder and prove the batched
history query actually returns rows.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.phil9000 import analysis
from tests.helpers import KITCHEN, OFFICE

pytest.importorskip("pytest_homeassistant_custom_component")


async def _recorder_ready(hass: HomeAssistant) -> None:
    """Flush the recorder so queued events are committed and queryable.

    The harness helper forces a commit, but with a burst of events one pass
    can leave work queued (the event listener and the commit task race on the
    recorder thread), so repeat until the queue is drained.
    """
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    for _ in range(3):
        await async_wait_recording_done(hass)


async def test_fetch_activity_returns_recorder_rows(hass: HomeAssistant, tmp_path) -> None:
    from homeassistant.const import EVENT_HOMEASSISTANT_START
    from homeassistant.helpers.recorder import async_initialize_recorder

    async_initialize_recorder(hass)
    assert await async_setup_component(
        hass, "recorder", {"recorder": {"db_url": f"sqlite:///{tmp_path}/recorder.db"}}
    )
    # The recorder only starts storing state changes when HA starts.
    hass.bus.async_fire(EVENT_HOMEASSISTANT_START)
    await _recorder_ready(hass)

    hass.states.async_set(OFFICE, "on")
    hass.states.async_set(OFFICE, "off")
    hass.states.async_set(KITCHEN, "on")
    await _recorder_ready(hass)

    rows = await analysis.async_fetch_activity(hass, [OFFICE, KITCHEN], 1000)
    assert rows, "the recorder has real rows: the fetch must return them"
    office_rows = [row for row in rows if row["entity_id"] == OFFICE]
    assert office_rows[0]["new_state"] == "off"  # newest first
    assert office_rows[0]["old_state"] == "on"
    kitchen_rows = [row for row in rows if row["entity_id"] == KITCHEN]
    assert kitchen_rows[0]["new_state"] == "on"
    # newest first
    assert rows == sorted(rows, key=lambda r: r["at"], reverse=True)


async def test_fetch_activity_without_recorder_is_an_explicit_error(
    hass: HomeAssistant,
) -> None:
    """No recorder installed: the panel must get a reason, not silence."""
    with pytest.raises(analysis.AnalysisUnavailableError, match="recorder"):
        await analysis.async_fetch_activity(hass, [OFFICE], 1000)
