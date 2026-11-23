"""Config/options flow tests plus corrupt-store robustness (harness)."""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.phil9000.const import (
    CONF_ENTITIES,
    CONF_EXCLUDE_ENTITIES,
    DEFAULT_OBSERVABLE_DOMAINS,
    DOMAIN,
    OPTION_ANALYSIS_DAYS,
    OPTION_ANALYSIS_ENABLED,
    OPTION_COOLDOWN_DEFAULT,
    OPTION_CUSTOM_PROMPT,
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


def _form_default(data_schema: vol.Schema | None, key_name: str) -> object:
    """The default value a data-schema field was shown with, if any."""
    assert data_schema is not None
    for marker in data_schema.schema:
        if isinstance(marker, vol.Marker) and marker.schema == key_name:
            default = getattr(marker, "default", vol.UNDEFINED)
            return default() if callable(default) else default
    raise AssertionError(f"{key_name!r} is not in the form schema")


@pytest.fixture(autouse=True)
def auto_enable_custom(enable_custom_integrations):  # noqa: ANN001, ARG001
    """Enable custom_components loading for every test in this module."""


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: ["binary_sensor", "switch"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENTITIES] == ["binary_sensor", "switch"]
    assert result["options"][OPTION_ANALYSIS_ENABLED] is False
    assert result["options"][OPTION_COOLDOWN_DEFAULT] == 3600


async def test_user_flow_preselects_common_domains(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    # A one-click default: presence-ish domains ticked out of the box.
    assert _form_default(result["data_schema"], CONF_ENTITIES) == sorted(DEFAULT_OBSERVABLE_DOMAINS)


async def test_user_flow_requires_domains_then_recovers(
    hass: HomeAssistant,
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ENTITIES: []})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_entities"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: ["binary_sensor"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow_updates_settings_only(
    hass: HomeAssistant,
) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)  # explicit specs: [OFFICE, KITCHEN]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            OPTION_COOLDOWN_DEFAULT: 1800,
            OPTION_ANALYSIS_ENABLED: True,
            OPTION_ANALYSIS_DAYS: 14,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPTION_ANALYSIS_ENABLED] is True
    assert entry.options[OPTION_ANALYSIS_DAYS] == 14
    assert entry.options[OPTION_COOLDOWN_DEFAULT] == 1800


async def test_options_flow_never_touches_observations(
    hass: HomeAssistant,
) -> None:
    seed_behaviours(hass, end_of_work_day())
    # What is observed lives in the panel; the settings form leaves data alone.
    entry = await setup_entry(
        hass,
        data={
            CONF_ENTITIES: ["binary_sensor", "light.desk"],
            CONF_EXCLUDE_ENTITIES: [KITCHEN],
        },
    )
    data_before = dict(entry.data)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    # No subscription fields on the form anymore.
    form_schema = result["data_schema"]
    assert form_schema is not None
    field_names = [key if isinstance(key, str) else key.schema for key in form_schema.schema]
    assert CONF_ENTITIES not in field_names

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            OPTION_COOLDOWN_DEFAULT: 3600,
            OPTION_ANALYSIS_ENABLED: False,
            OPTION_ANALYSIS_DAYS: 7,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.data) == data_before


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


async def test_options_flow_saves_a_valid_custom_prompt(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)

    # The form prefills with the built-in template, so a first save without
    # touching the field stores exactly that (behaviour unchanged).
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {OPTION_CUSTOM_PROMPT: "Look at this: {summary} — say JSON."},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPTION_CUSTOM_PROMPT] == "Look at this: {summary} — say JSON."


async def test_options_flow_rejects_bad_custom_prompts(hass: HomeAssistant) -> None:
    seed_behaviours(hass, end_of_work_day())
    entry = await setup_entry(hass)

    for bad in (
        "Never show the history.",  # no {summary}
        "Broken {summary} with a lone } brace",  # stray brace
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {OPTION_CUSTOM_PROMPT: bad}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"]
        assert result["errors"]["base"] in ("prompt_needs_summary", "prompt_invalid")
        assert not entry.options.get(OPTION_CUSTOM_PROMPT)

    # A blank prompt clears back to the built-in (None), not an error.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPTION_CUSTOM_PROMPT: ""}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPTION_CUSTOM_PROMPT] is None
