"""Notification helper shared by candidate suggestions and analyser results."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import OPTION_NOTIFY_ENTITY

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_notify(
    hass: HomeAssistant, entry: ConfigEntry, title: str, message: str
) -> None:
    """Send via the configured notify entity, else a persistent notification.

    Never raises: notification failures must not hide detections or results.
    """
    notify_entity = entry.options.get(OPTION_NOTIFY_ENTITY)
    try:
        if notify_entity:
            await hass.services.async_call(
                "notify",
                "send_message",
                {"title": title, "message": message},
                target={"entity_id": notify_entity},
                blocking=True,
            )
        else:
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {"title": title, "message": message},
                blocking=True,
            )
    except Exception:  # noqa: BLE001 — a missing notifier must not hide anything
        _LOGGER.exception("Failed to deliver HAL1976 notification: %s", title)
