"""Websocket API: PHIL9000's data surface for the basic frontend (Milestone 4).

Commands are registered once per HA run from the integration's async_setup;
handlers resolve the current runtime from hass.data on every message, so
config-entry reloads are transparent to connected clients.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from . import analysis
from .activity import DEFAULT_LIMIT
from .const import (
    ATTR_BEHAVIOUR_ID,
    BEHAVIOURS_FILE,
    CONF_ENTITIES,
    CONF_EXCLUDE_ENTITIES,
    CONFIG_DIR,
    DOMAIN,
    OBSERVABLE_DOMAINS,
    domain_label_parts,
)
from .models import BehaviourAction, BehaviourStatus
from .subscriptions import is_valid_entity_id

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
    runtime = current_runtime(hass)
    if runtime is None:
        return []
    return runtime.subscriptions.expand(hass.states.async_entity_ids(), runtime.is_noise)


def _entry(hass: HomeAssistant):  # noqa: ANN202 — ConfigEntry (HA type, purity guard)
    """The (single) PHIL9000 config entry, or None."""
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def async_setup(hass: HomeAssistant) -> None:
    """Register the phil9000 websocket commands (idempotent per HA run)."""
    import voluptuous as vol  # function-local: purity guard
    from homeassistant.components import (  # function-local: purity guard
        websocket_api,
    )

    def _require(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
        *,
        admin: bool = False,
    ) -> Phil9000Runtime | None:
        """The runtime for this message, or None after sending the right error.

        ``admin`` gates commands that amend settings: confirmed behaviours
        run service calls with the integration's authority, so only admin
        users may define or trigger them.
        """
        runtime = current_runtime(hass)
        if runtime is None:
            connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "PHIL9000 is not loaded")
            return None
        if admin and (connection.user is None or not connection.user.is_admin):
            connection.send_error(
                msg["id"], websocket_api.ERR_UNAUTHORIZED, "PHIL9000 requires an admin user"
            )
            return None
        return runtime

    @websocket_api.websocket_command({"type": "phil9000/behaviours"})
    @websocket_api.async_response
    async def handle_behaviours(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        if (runtime := _require(hass, connection, msg)) is None:
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
        if (runtime := _require(hass, connection, msg, admin=True)) is None:
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
        if (runtime := _require(hass, connection, msg, admin=True)) is None:
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

    @websocket_api.websocket_command(
        {
            "type": "phil9000/activity",
            vol.Optional("limit", default=DEFAULT_LIMIT): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=2000)
            ),
        }
    )
    @websocket_api.async_response
    async def handle_activity(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        if _require(hass, connection, msg) is None:
            return
        entities = _observed_entities(hass)
        # Diagnostics the panel shows under the heading: when the feed looks
        # wrong, "N rows / M entities / K h window" answers server-vs-config
        # questions without reading logs.
        info = {
            "observed": len(entities),
            "window_hours": analysis.ACTIVITY_LOOKBACK_HOURS,
            "limit": msg["limit"],
            # A short list is usually the real answer, so send it when small.
            "observed_entities": entities if len(entities) <= 10 else None,
        }
        try:
            items = await analysis.async_fetch_activity(hass, entities, msg["limit"])
        except analysis.AnalysisUnavailableError as err:
            # Empty feed plus a hint (no recorder, or the entities are excluded
            # from it) is more useful to the panel than a hard error.
            connection.send_result(msg["id"], {"items": [], "hint": str(err), **info})
            return
        connection.send_result(msg["id"], {"items": items, **info})

    @websocket_api.websocket_command({"type": "phil9000/subscribe_activity"})
    @websocket_api.async_response
    async def handle_subscribe_activity(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        """Live activity feed: one message per observed state change."""
        if (runtime := _require(hass, connection, msg)) is None:
            return
        connection.send_result(msg["id"])

        def push(row: dict[str, Any]) -> None:
            connection.send_message(websocket_api.event_message(msg["id"], row))

        def unsubscribe() -> None:
            if push in runtime.activity_subscribers:
                runtime.activity_subscribers.remove(push)

        runtime.activity_subscribers.append(push)
        connection.subscriptions[msg["id"]] = unsubscribe

    @websocket_api.websocket_command({"type": "phil9000/analysis_log"})
    @websocket_api.async_response
    async def handle_analysis_log(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        """Recent LLM analysis runs, newest first (the panel's Analysis log)."""
        if (runtime := _require(hass, connection, msg)) is None:
            return
        connection.send_result(msg["id"], {"entries": list(reversed(runtime.analysis_log))})

    @websocket_api.websocket_command({"type": "phil9000/subscribe_analysis_log"})
    @websocket_api.async_response
    async def handle_subscribe_analysis_log(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        """Live analysis log: one message per run event (start, progress, result)."""
        if (runtime := _require(hass, connection, msg)) is None:
            return
        connection.send_result(msg["id"])

        def push(entry: dict[str, Any]) -> None:
            connection.send_message(websocket_api.event_message(msg["id"], entry))

        def unsubscribe() -> None:
            if push in runtime.analysis_log_subscribers:
                runtime.analysis_log_subscribers.remove(push)

        runtime.analysis_log_subscribers.append(push)
        connection.subscriptions[msg["id"]] = unsubscribe

    @websocket_api.websocket_command({"type": "phil9000/run_analysis"})
    @websocket_api.async_response
    async def handle_run_analysis(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        if (runtime := _require(hass, connection, msg, admin=True)) is None:
            return
        entry = _entry(hass)
        try:
            summary = await analysis.async_run_analysis(hass, entry, runtime, trigger="manual")
        except analysis.AnalysisUnavailableError as err:
            # Friendly expected failures: no recorder/agent, agent error, busy.
            connection.send_error(msg["id"], websocket_api.ERR_NOT_SUPPORTED, str(err))
            return
        connection.send_result(msg["id"], summary)

    @websocket_api.websocket_command(
        {
            "type": "phil9000/set_name",
            vol.Required(ATTR_BEHAVIOUR_ID): str,
            vol.Required("name"): vol.All(str, vol.Length(min=1, max=120)),
        }
    )
    @websocket_api.async_response
    async def handle_set_name(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        if (runtime := _require(hass, connection, msg, admin=True)) is None:
            return
        try:
            await runtime.async_rename(hass, msg[ATTR_BEHAVIOUR_ID], msg["name"].strip())
        except ValueError as err:
            connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, str(err))
            return
        connection.send_result(msg["id"])

    @websocket_api.websocket_command({"type": "phil9000/monitoring"})
    @websocket_api.async_response
    async def handle_monitoring(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        """What the entry observes: domain specs, extra entities, mutes."""
        if (runtime := _require(hass, connection, msg)) is None:
            return
        subscriptions = runtime.subscriptions
        domain_options = []
        for domain in sorted(OBSERVABLE_DOMAINS):
            label, hint = domain_label_parts(domain)
            domain_options.append({"value": domain, "label": label, "hint": hint})
        connection.send_result(
            msg["id"],
            {
                "domains": sorted(subscriptions.domains),
                "domain_options": domain_options,
                "entities": sorted(subscriptions.entity_ids),
                "muted": sorted(subscriptions.excluded),
                "observed": _observed_entities(hass),
            },
        )

    @websocket_api.websocket_command(
        {"type": "phil9000/mute_entity", vol.Required("entity_id"): str}
    )
    @websocket_api.async_response
    async def handle_mute_entity(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        """Stop observing an entity (persisted in the entry's exclude list).

        The update listener swaps the runtime subscriptions in place, so the
        mute applies to the next state change and live panel feeds keep
        working — no entry reload.
        """
        await _async_set_mute(hass, connection, msg, mute=True)

    @websocket_api.websocket_command(
        {"type": "phil9000/unmute_entity", vol.Required("entity_id"): str}
    )
    @websocket_api.async_response
    async def handle_unmute_entity(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        """Resume observing a previously muted entity."""
        await _async_set_mute(hass, connection, msg, mute=False)

    async def _async_set_mute(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any], *, mute: bool
    ) -> None:
        if _require(hass, connection, msg, admin=True) is None:
            return
        entry = _entry(hass)
        entity_id = msg["entity_id"].strip().lower()
        if not is_valid_entity_id(entity_id):
            connection.send_error(
                msg["id"], websocket_api.ERR_INVALID_FORMAT, f"{entity_id!r} is not an entity id"
            )
            return
        excluded = set(entry.data.get(CONF_EXCLUDE_ENTITIES, ()))
        if mute:
            excluded.add(entity_id)
        else:
            excluded.discard(entity_id)
        if excluded != set(entry.data.get(CONF_EXCLUDE_ENTITIES, ())):
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_EXCLUDE_ENTITIES: sorted(excluded)}
            )
            # Let the update listener swap the subscriptions before we answer,
            # so the very next event already honours the change.
            await hass.async_block_till_done()
        connection.send_result(msg["id"], {"muted": sorted(excluded)})

    @websocket_api.websocket_command(
        {
            "type": "phil9000/set_observed",
            vol.Required("entity_id"): str,
            vol.Required("observed"): bool,
        }
    )
    @websocket_api.async_response
    async def handle_set_observed(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        """Tick-box semantics for the panel's entity browser.

        observed=True  → unmute, and make sure the entity is covered (added as
                         an explicit spec when its domain isn't subscribed).
        observed=False → if currently observed: drop any explicit spec and
                         mute it (domain coverage alone would keep it live).
        Anything not observed stays untouched (no phantom mutes).
        """
        if (runtime := _require(hass, connection, msg, admin=True)) is None:
            return
        entry = _entry(hass)
        entity_id = msg["entity_id"].strip().lower()
        if not is_valid_entity_id(entity_id):
            connection.send_error(
                msg["id"], websocket_api.ERR_INVALID_FORMAT, f"{entity_id!r} is not an entity id"
            )
            return
        specs = set(entry.data.get(CONF_ENTITIES, ()))
        excluded = set(entry.data.get(CONF_EXCLUDE_ENTITIES, ()))
        if msg["observed"]:
            excluded.discard(entity_id)
            if entity_id not in specs and entity_id.partition(".")[0] not in specs:
                specs.add(entity_id)
        elif runtime.subscriptions.matches(entity_id, runtime.is_noise):
            specs.discard(entity_id)
            excluded.add(entity_id)
        data = {**entry.data, CONF_ENTITIES: sorted(specs)}
        if excluded:
            data[CONF_EXCLUDE_ENTITIES] = sorted(excluded)
        else:
            data.pop(CONF_EXCLUDE_ENTITIES, None)
        if data != dict(entry.data):
            hass.config_entries.async_update_entry(entry, data=data)
            await hass.async_block_till_done()  # listener swaps subscriptions first
        connection.send_result(
            msg["id"],
            {"muted": sorted(excluded), "explicit": sorted(s for s in specs if "." in s)},
        )

    @websocket_api.websocket_command(
        {"type": "phil9000/set_domains", vol.Required("domains"): [str]}
    )
    @websocket_api.async_response
    async def handle_set_domains(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        """Replace the subscribed domains (the panel's domain checklist).

        Explicit entity ids added from the panel survive untouched, as do the
        exclusions; only the bare-domain specs are replaced. Applies in place.
        """
        if _require(hass, connection, msg, admin=True) is None:
            return
        entry = _entry(hass)
        wanted = {domain.strip().lower() for domain in msg["domains"]}
        unknown = sorted(wanted - OBSERVABLE_DOMAINS)
        if unknown:
            connection.send_error(
                msg["id"],
                websocket_api.ERR_INVALID_FORMAT,
                f"not observable domains: {', '.join(unknown)}",
            )
            return
        specs = {s for s in entry.data.get(CONF_ENTITIES, ()) if "." in s} | wanted
        data = {**entry.data, CONF_ENTITIES: sorted(specs)}
        if data != dict(entry.data):
            hass.config_entries.async_update_entry(entry, data=data)
            await hass.async_block_till_done()  # listener swaps subscriptions first
        connection.send_result(msg["id"], {"domains": sorted(wanted)})

    for handler in (
        handle_behaviours,
        handle_set_status,
        handle_set_actions,
        handle_activity,
        handle_subscribe_activity,
        handle_analysis_log,
        handle_subscribe_analysis_log,
        handle_run_analysis,
        handle_set_name,
        handle_monitoring,
        handle_mute_entity,
        handle_unmute_entity,
        handle_set_observed,
        handle_set_domains,
    ):
        websocket_api.async_register_command(hass, handler)
