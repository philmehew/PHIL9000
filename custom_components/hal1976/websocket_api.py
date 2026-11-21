"""Websocket API: HAL1976's data surface for the basic frontend (Milestone 4).

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
    CONFIG_DIR,
    DOMAIN,
)
from .models import BehaviourAction, BehaviourStatus

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import HalRuntime

_LOGGER = logging.getLogger(__name__)


def current_runtime(hass: HomeAssistant) -> HalRuntime | None:
    """The runtime of the (single) loaded HAL1976 config entry, if any."""
    for runtime in hass.data.get(DOMAIN, {}).values():
        return runtime
    return None


def _observed_entities(hass: HomeAssistant) -> list[str]:
    for entry in hass.config_entries.async_entries(DOMAIN):
        return list(entry.data.get(CONF_ENTITIES, []))
    return []


def async_setup(hass: HomeAssistant) -> None:
    """Register the hal1976 websocket commands (idempotent per HA run)."""
    import voluptuous as vol  # function-local: purity guard
    from homeassistant.components import (  # function-local: purity guard
        websocket_api,
    )

    @websocket_api.async_response
    @websocket_api.websocket_command({"type": "hal1976/behaviours"})
    async def handle_behaviours(
        hass: HomeAssistant, connection: Any, msg: dict[str, Any]
    ) -> None:
        runtime = current_runtime(hass)
        if runtime is None:
            connection.send_error(
                msg["id"], websocket_api.ERR_NOT_FOUND, "HAL1976 is not loaded"
            )
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

    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            "type": "hal1976/set_status",
            vol.Required(ATTR_BEHAVIOUR_ID): str,
            vol.Required("status"): vol.In(
                [status.value for status in BehaviourStatus]
            ),
        }
    )
    async def handle_set_status(
        hass: HomeAssistant, connection: Any, msg: dict[str, Any]
    ) -> None:
        runtime = current_runtime(hass)
        if runtime is None:
            connection.send_error(
                msg["id"], websocket_api.ERR_NOT_FOUND, "HAL1976 is not loaded"
            )
            return
        try:
            await runtime.async_set_status(
                hass, msg[ATTR_BEHAVIOUR_ID], BehaviourStatus(msg["status"])
            )
        except ValueError as err:
            connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, str(err))
            return
        connection.send_result(msg["id"])

    @websocket_api.async_response
    @websocket_api.websocket_command(
        {
            "type": "hal1976/set_actions",
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
    async def handle_set_actions(
        hass: HomeAssistant, connection: Any, msg: dict[str, Any]
    ) -> None:
        runtime = current_runtime(hass)
        if runtime is None:
            connection.send_error(
                msg["id"], websocket_api.ERR_NOT_FOUND, "HAL1976 is not loaded"
            )
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
