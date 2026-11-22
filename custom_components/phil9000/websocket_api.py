"""Websocket API: PHIL9000's data surface for the basic frontend (Milestone 4).

Commands are registered once per HA run from the integration's async_setup;
handlers resolve the current runtime from hass.data on every message, so
config-entry reloads are transparent to connected clients.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .const import (
    ATTR_BEHAVIOUR_ID,
    BEHAVIOURS_FILE,
    CONF_ENTITIES,
    CONF_EXCLUDE_ENTITIES,
    CONFIG_DIR,
    DOMAIN,
)
from .models import BehaviourAction, BehaviourStatus
from .subscriptions import Subscriptions, registry_is_noise

if TYPE_CHECKING:
    from homeassistant.components.websocket_api import ActiveConnection
    from homeassistant.core import HomeAssistant

    from . import Phil9000Runtime

_LOGGER = logging.getLogger(__name__)


def current_runtime(hass: HomeAssistant) -> Phil9000Runtime | None:
    """The runtime of the (single) loaded PHIL9000 config entry, if any."""
    for runtime in hass.data.get(DOMAIN, {}).values():
        return runtime
    return None


def _observed_entities(hass: HomeAssistant) -> list[str]:
    """Concrete entities currently observed (whole-domain specs expanded)."""
    from homeassistant.helpers.entity_registry import (  # function-local: purity guard
        async_get as async_get_entity_registry,
    )

    is_noise = registry_is_noise(async_get_entity_registry(hass))
    for entry in hass.config_entries.async_entries(DOMAIN):
        subscriptions = Subscriptions(
            entry.data.get(CONF_ENTITIES, []), entry.data.get(CONF_EXCLUDE_ENTITIES, ())
        )
        return subscriptions.expand(hass.states.async_entity_ids(), is_noise)
    return []


def async_setup(hass: HomeAssistant) -> None:
    """Register the phil9000 websocket commands (idempotent per HA run)."""
    import voluptuous as vol  # function-local: purity guard
    from homeassistant.components import (  # function-local: purity guard
        websocket_api,
    )

    @websocket_api.websocket_command({"type": "phil9000/behaviours"})
    @websocket_api.async_response
    async def handle_behaviours(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        runtime = current_runtime(hass)
        if runtime is None:
            connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "PHIL9000 is not loaded")
            return
        connection.send_result(
            msg["id"],
            {
                "path": hass.config.path(CONFIG_DIR, BEHAVIOURS_FILE),
                "observed_entities": _observed_entities(hass),
                "behaviours": [
                    {
                        **behaviour.to_dict(),
                        "last_detection": runtime.last_detections.get(behaviour.id),
                    }
                    for behaviour in runtime.engine.behaviours.values()
                ],
            },
        )

    @websocket_api.websocket_command(
        {
            "type": "phil9000/set_status",
            vol.Required(ATTR_BEHAVIOUR_ID): str,
            vol.Required("status"): vol.In([status.value for status in BehaviourStatus]),
        }
    )
    @websocket_api.async_response
    async def handle_set_status(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        runtime = current_runtime(hass)
        if runtime is None:
            connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "PHIL9000 is not loaded")
            return
        try:
            await runtime.async_set_status(
                hass, msg[ATTR_BEHAVIOUR_ID], BehaviourStatus(msg["status"])
            )
        except ValueError as err:
            connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, str(err))
            return
        connection.send_result(msg["id"])

    @websocket_api.websocket_command(
        {
            "type": "phil9000/set_actions",
            vol.Required(ATTR_BEHAVIOUR_ID): str,
            vol.Required("actions"): [
                vol.Schema(
                    {
                        vol.Required("domain"): str,
                        vol.Required("service"): str,
                        vol.Optional("entity_id", default=""): str,
                        vol.Optional("data", default=dict): dict,
                    }
                )
            ],
        }
    )
    @websocket_api.async_response
    async def handle_set_actions(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        runtime = current_runtime(hass)
        if runtime is None:
            connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "PHIL9000 is not loaded")
            return
        actions = [
            BehaviourAction(
                action["domain"], action["service"], action["entity_id"], dict(action["data"])
            )
            for action in msg["actions"]
        ]
        try:
            await runtime.async_set_actions(hass, msg[ATTR_BEHAVIOUR_ID], actions)
        except ValueError as err:
            connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, str(err))
            return
        connection.send_result(msg["id"])

    for handler in (handle_behaviours, handle_set_status, handle_set_actions):
        websocket_api.async_register_command(hass, handler)
