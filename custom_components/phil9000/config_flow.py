"""Config flow for PHIL9000: domain subscriptions + options.

This module is Home Assistant-facing (only ever imported inside HA).

Setup asks for entity *domains* (a handful of toggles instead of hundreds of
entities); the options flow re-edits that checklist plus analysis settings.
Per-entity management — extra observations and mutes — lives in the panel,
and saving the options form preserves whatever the panel has set.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .analyser import DEFAULT_LOOKBACK_DAYS
from .const import (
    CONF_ENTITIES,
    DEFAULT_COOLDOWN_DEFAULT,
    DEFAULT_OBSERVABLE_DOMAINS,
    DOMAIN,
    OBSERVABLE_DOMAIN_LABELS,
    OBSERVABLE_DOMAINS,
    OPTION_AGENT_ID,
    OPTION_ANALYSIS_DAYS,
    OPTION_ANALYSIS_ENABLED,
    OPTION_COOLDOWN_DEFAULT,
    OPTION_NOTIFY_ENTITY,
)
from .subscriptions import split_specs


def _domain_picker() -> SelectSelector:
    """Checklist of the observable entity domains (a dozen or so options)."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=domain, label=OBSERVABLE_DOMAIN_LABELS.get(domain, domain))
                for domain in sorted(OBSERVABLE_DOMAINS)
            ],
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


class Phil9000ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup: pick the entity domains PHIL9000 should observe."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            domains = user_input.get(CONF_ENTITIES) or []
            if not domains:
                errors["base"] = "no_entities"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="PHIL9000",
                    data={CONF_ENTITIES: sorted(domains)},
                    options={
                        OPTION_AGENT_ID: None,
                        OPTION_NOTIFY_ENTITY: None,
                        OPTION_COOLDOWN_DEFAULT: DEFAULT_COOLDOWN_DEFAULT,
                        OPTION_ANALYSIS_ENABLED: False,
                        OPTION_ANALYSIS_DAYS: DEFAULT_LOOKBACK_DAYS,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ENTITIES, default=sorted(DEFAULT_OBSERVABLE_DOMAINS)
                ): _domain_picker()
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return Phil9000OptionsFlow()


class Phil9000OptionsFlow(OptionsFlow):
    """Observation refinement (domains, extra entities, exclusions) and settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            agent_id = (user_input.get(OPTION_AGENT_ID) or "").strip() or None
            notify_entity = (user_input.get(OPTION_NOTIFY_ENTITY) or "").strip() or None
            # The checklist only covers domains: explicit entity ids picked in
            # the panel survive, and exclusions (mutes) are left alone.
            _, current_entity_ids = split_specs(self.config_entry.data.get(CONF_ENTITIES, []))
            observed = sorted(set(user_input.get(CONF_ENTITIES) or []) | current_entity_ids)
            if observed:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_ENTITIES: observed},
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
        current_domains, _ = split_specs(self.config_entry.data.get(CONF_ENTITIES, []))
        schema = vol.Schema(
            {
                vol.Required(CONF_ENTITIES, default=sorted(current_domains)): _domain_picker(),
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
