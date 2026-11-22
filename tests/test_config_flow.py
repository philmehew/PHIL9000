"""Config/options flow tests plus corrupt-store robustness (harness)."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.phil9000.const import (
    CONF_ENTITIES,
    DOMAIN,
    OPTION_ANALYSIS_DAYS,
    OPTION_ANALYSIS_ENABLED,
    OPTION_COOLDOWN_DEFAULT,
)
from custom_components.phil9000.seed import end_of_work_day
from tests.helpers import (
    KITCHEN,
    OFFICE,
    behaviours_path,
    seed_behaviours,
    setup_entry,
)

pytest.importorskip("pytest_homeassistant_custom_component")


@pytest.fixture(autouse=True)
def auto_enable_custom(enable_custom_integrations):  # noqa: ANN001, ARG001
    """Enable custom_components loading for every test in this module."""


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    hass.states.async_set(OFFICE, "on")
    hass.states.async_set(KITCHEN, "off")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: [KITCHEN, OFFICE]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENTITIES] == sorted([OFFICE, KITCHEN])
    assert result["options"][OPTION_ANALYSIS_ENABLED] is False
    assert result["options"][OPTION_COOLDOWN_DEFAULT] == 3600


async def test_user_flow_requires_entities_then_recovers(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(OFFICE, "on")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ENTITIES: []})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_entities"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: [OFFICE]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow_updates_entities_and_settings(
    hass: HomeAssistant,
) -> None:
    seed_behaviours(hass, end_of_work_day())
    hass.states.async_set(OFFICE, "on")
    hass.states.async_set(KITCHEN, "off")
    entry = await setup_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENTITIES: [KITCHEN],
            OPTION_COOLDOWN_DEFAULT: 1800,
            OPTION_ANALYSIS_ENABLED: True,
            OPTION_ANALYSIS_DAYS: 14,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_ENTITIES] == [KITCHEN]  # data updated, sorted
    assert entry.options[OPTION_ANALYSIS_ENABLED] is True
    assert entry.options[OPTION_ANALYSIS_DAYS] == 14
    assert entry.options[OPTION_COOLDOWN_DEFAULT] == 1800


async def test_corrupt_behaviours_yaml_delays_setup(hass: HomeAssistant) -> None:
    # A behaviour entry missing its required fields must not boot PHIL9000
    # "empty" (a later save would destroy the file) — setup is retried.
    behaviours_path(hass).parent.mkdir(parents=True, exist_ok=True)
    behaviours_path(hass).write_text("behaviours:\n  - name: Broken\n")

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN, source=SOURCE_USER, data={CONF_ENTITIES: [OFFICE, KITCHEN]}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
