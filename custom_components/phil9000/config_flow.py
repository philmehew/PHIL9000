"""Config flow for PHIL9000: entity selection + options.

This module is Home Assistant-facing (only ever imported inside HA).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .analyser import DEFAULT_LOOKBACK_DAYS
from .const import (
    CONF_ENTITIES,
    DEFAULT_COOLDOWN_DEFAULT,
    DOMAIN,
    OBSERVABLE_DOMAINS,
    OPTION_AGENT_ID,
    OPTION_ANALYSIS_DAYS,
    OPTION_ANALYSIS_ENABLED,
    OPTION_COOLDOWN_DEFAULT,
    OPTION_NOTIFY_ENTITY,
)


def _observation_candidates(hass: HomeAssistant, *, extra: Iterable[str] = ()) -> list[str]:
    """Entity ids worth observing: observable domains minus hidden noise.

    Skips disabled/hidden entities and diagnostic/config ones (firmware
    sensors, buttons, ...). ``extra`` keeps already-selected ids visible even
    if they no longer pass the filter.
    """
    registry = async_get_entity_registry(hass)
    candidates = set(extra)
    for entity_id in hass.states.async_entity_ids():
        if entity_id.partition(".")[0] not in OBSERVABLE_DOMAINS:
            continue
        entry = registry.async_get(entity_id)
        if entry is not None:
            if entry.disabled_by is not None or entry.hidden_by is not None:
                continue
            if entry.entity_category in (EntityCategory.CONFIG, EntityCategory.DIAGNOSTIC):
                continue
        candidates.add(entity_id)
    return sorted(candidates)


def _entity_picker(candidates: list[str]) -> EntitySelector:
    """Searchable multi-select entity picker."""
    return EntitySelector(EntitySelectorConfig(include_entities=candidates, multiple=True))


class Phil9000ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup: pick the entities PHIL9000 should observe."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            entities = user_input.get(CONF_ENTITIES) or []
            if not entities:
                errors["base"] = "no_entities"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="PHIL9000",
                    data={CONF_ENTITIES: sorted(entities)},
                    options={
                        OPTION_AGENT_ID: None,
                        OPTION_NOTIFY_ENTITY: None,
                        OPTION_COOLDOWN_DEFAULT: DEFAULT_COOLDOWN_DEFAULT,
                        OPTION_ANALYSIS_ENABLED: False,
                        OPTION_ANALYSIS_DAYS: DEFAULT_LOOKBACK_DAYS,
                    },
                )

        candidates = _observation_candidates(self.hass)
        schema = vol.Schema(
            {
                vol.Required(CONF_ENTITIES): _entity_picker(candidates),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return Phil9000OptionsFlow()


class Phil9000OptionsFlow(OptionsFlow):
    """Conversation agent (for the future LLM analyser), notify target, cooldowns."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            agent_id = (user_input.get(OPTION_AGENT_ID) or "").strip() or None
            notify_entity = (user_input.get(OPTION_NOTIFY_ENTITY) or "").strip() or None
            entities = sorted(user_input.get(CONF_ENTITIES) or [])
            if entities:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data={**self.config_entry.data, CONF_ENTITIES: entities}
                )
            return self.async_create_entry(
                title="",
                data={
                    OPTION_AGENT_ID: agent_id,
                    OPTION_NOTIFY_ENTITY: notify_entity,
                    OPTION_COOLDOWN_DEFAULT: int(
                        user_input.get(OPTION_COOLDOWN_DEFAULT, DEFAULT_COOLDOWN_DEFAULT)
                    ),
                    OPTION_ANALYSIS_ENABLED: bool(user_input.get(OPTION_ANALYSIS_ENABLED, False)),
                    OPTION_ANALYSIS_DAYS: int(
                        user_input.get(OPTION_ANALYSIS_DAYS, DEFAULT_LOOKBACK_DAYS)
                    ),
                },
            )

        current = self.config_entry.options
        candidates = _observation_candidates(
            self.hass, extra=self.config_entry.data.get(CONF_ENTITIES, [])
        )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ENTITIES,
                    default=sorted(self.config_entry.data.get(CONF_ENTITIES, [])),
                ): _entity_picker(candidates),
                # Selector defaults are validated by voluptuous: only supply
                # one when a value is actually configured, otherwise the key
                # stays absent (blank = unset).
                vol.Optional(
                    OPTION_AGENT_ID,
                    default=current.get(OPTION_AGENT_ID) or vol.UNDEFINED,
                ): EntitySelector(EntitySelectorConfig(domain="conversation")),
                vol.Optional(
                    OPTION_NOTIFY_ENTITY,
                    default=current.get(OPTION_NOTIFY_ENTITY) or vol.UNDEFINED,
                ): EntitySelector(EntitySelectorConfig(domain="notify")),
                vol.Optional(
                    OPTION_COOLDOWN_DEFAULT,
                    default=current.get(OPTION_COOLDOWN_DEFAULT, DEFAULT_COOLDOWN_DEFAULT),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=86400, step=60, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    OPTION_ANALYSIS_ENABLED,
                    default=current.get(OPTION_ANALYSIS_ENABLED, False),
                ): BooleanSelector(),
                vol.Optional(
                    OPTION_ANALYSIS_DAYS,
                    default=current.get(OPTION_ANALYSIS_DAYS, DEFAULT_LOOKBACK_DAYS),
                ): NumberSelector(
                    NumberSelectorConfig(min=1, max=90, step=1, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
