"""PHIL9000 — learns household behaviour from Home Assistant sensor data.

The pure domain logic (models, detection engine, YAML store) lives in this
package with **no Home Assistant imports**: every ``homeassistant.*`` import
here is function-local so the core modules stay importable and testable
without Home Assistant installed. ``tests/test_purity.py`` guards this
boundary. This module is the thin adapter over the event bus, services,
and storage: detection and action selection are decided by the pure
engine; the adapter only executes them against Home Assistant.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import analysis, notifier, websocket_api
from .activity import is_recovery
from .const import (
    ANALYSIS_LOG_FILE,
    ATTR_ACTIONS,
    ATTR_BEHAVIOUR_ID,
    ATTR_BEHAVIOUR_NAME,
    ATTR_CONFIDENCE,
    ATTR_MATCHED,
    ATTR_OCCURRENCES,
    ATTR_STATUS,
    BEHAVIOURS_FILE,
    CONF_ENTITIES,
    CONF_EXCLUDE_ENTITIES,
    CONFIG_DIR,
    DOMAIN,
    EVENT_BEHAVIOUR_DETECTED,
    OPTION_ANALYSIS_ENABLED,
    PANEL_URL_PATH,
    PANEL_WEBCOMPONENT,
    SAVE_DEBOUNCE_SECONDS,
    SERVICE_CONFIRM_BEHAVIOUR,
    SERVICE_DISMISS_BEHAVIOUR,
    SERVICE_RUN_ANALYSIS,
    STATIC_URL_PATH,
    UI_REGISTERED_FLAG,
)
from .detect import Detection, DetectionEngine
from .models import (
    Behaviour,
    BehaviourAction,
    BehaviourStatus,
    BehaviourStep,
    Observation,
)
from .subscriptions import Subscriptions, registry_is_noise
from .yaml_store import AnalysisLogStore, BehaviourStore

__all__ = [
    "Behaviour",
    "BehaviourAction",
    "BehaviourStatus",
    "BehaviourStep",
    "Detection",
    "DetectionEngine",
    "Observation",
    "BehaviourStore",
]

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant, ServiceCall

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Phil9000Runtime:
    """Per-config-entry runtime state, stored in hass.data[DOMAIN]."""

    engine: DetectionEngine
    store: BehaviourStore
    subscriptions: Subscriptions
    is_noise: Callable[[str], bool]
    # Snapshot of entry.options at setup/reload; the update listener only
    # reloads when options actually change (entity selection swaps in place).
    options_snapshot: dict = field(default_factory=dict)
    # Candidate ids already nudged to the user this session (one nudge per
    # candidate per run; a fresh HA start allows a fresh nudge).
    suggested: set[str] = field(default_factory=set)
    # behaviour_id -> payload of the most recent detection (in-memory
    # evidence shown in the UI; the observation history itself is HA's).
    last_detections: dict[str, dict] = field(default_factory=dict)
    # Panel subscribers for the live activity feed: callbacks taking one
    # feed row (see activity.py) and never raising.
    activity_subscribers: list[Callable[[dict], None]] = field(default_factory=list)
    # True while an LLM analysis pass is running (one run at a time).
    analysis_running: bool = False
    # Whether a debounced occurrence-save is already scheduled.
    _save_pending: bool = False
    # Ring buffer of recent analysis runs (oldest first), entries
    # {"at": iso-utc, "level": "info"|"error", "message": str}: what the
    # analyser did and what came back, shown in the panel's Analysis log.
    analysis_log: list[dict] = field(default_factory=list)
    analysis_log_store: AnalysisLogStore | None = None
    # Panel subscribers for the live analysis log (same contract as above).
    analysis_log_subscribers: list[Callable[[dict], None]] = field(default_factory=list)

    # -- mutations shared by services and the websocket API --------------

    def require_behaviour(self, behaviour_id: str) -> Behaviour:
        behaviour = self.engine.behaviours.get(behaviour_id)
        if behaviour is None:
            raise ValueError(f"unknown behaviour id {behaviour_id!r}")
        return behaviour

    async def async_set_status(
        self, hass: HomeAssistant, behaviour_id: str, status: BehaviourStatus
    ) -> Behaviour:
        """Set a behaviour's status (idempotent) and persist immediately."""
        behaviour = self.require_behaviour(behaviour_id)
        if behaviour.status is not status:
            behaviour.status = status
            await _async_save(hass, self)
        return behaviour

    async def async_set_actions(
        self, hass: HomeAssistant, behaviour_id: str, actions: list[BehaviourAction]
    ) -> Behaviour:
        """Replace a behaviour's actions and persist immediately."""
        behaviour = self.require_behaviour(behaviour_id)
        behaviour.actions = list(actions)
        await _async_save(hass, self)
        return behaviour

    async def async_rename(self, hass: HomeAssistant, behaviour_id: str, name: str) -> Behaviour:
        """Give a behaviour the user's own name; LLM refinements then keep it."""
        behaviour = self.require_behaviour(behaviour_id)
        behaviour.name = name
        behaviour.custom_name = True
        await _async_save(hass, self)
        return behaviour

    def schedule_save(self, hass: HomeAssistant) -> None:
        """Persist soon, collapsing bursts of occurrence-counter bumps.

        Only the debounce path uses this: explicit user actions (confirm,
        rename, edit actions) still save immediately.
        """
        if self._save_pending:
            return
        self._save_pending = True
        from datetime import datetime  # function-local: purity guard

        from homeassistant.helpers.event import (  # function-local: purity guard
            async_call_later,
        )

        async def _flush(now: datetime) -> None:  # noqa: ARG001
            self._save_pending = False
            await _async_save(hass, self)

        async_call_later(hass, SAVE_DEBOUNCE_SECONDS, _flush)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: ARG001
    hass.data.setdefault(DOMAIN, {})
    websocket_api.async_setup(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load behaviours from YAML, start the engine, watch the event bus."""
    import voluptuous as vol  # function-local: purity guard
    from homeassistant.config_entries import (  # function-local: purity guard
        ConfigEntryNotReady,
    )
    from homeassistant.const import EVENT_STATE_CHANGED  # function-local: purity guard

    store = BehaviourStore(hass.config.path(CONFIG_DIR, BEHAVIOURS_FILE))
    try:
        behaviours = await hass.async_add_executor_job(store.load)
    except Exception as err:
        # Never start up "empty" over a damaged file: the next save would
        # overwrite it. Fail the setup loudly instead and let the user fix
        # or restore the YAML.
        _LOGGER.exception(
            "PHIL9000 could not load %s — fix or restore the file, then retry",
            store.path,
        )
        raise ConfigEntryNotReady(f"{store.path} is invalid: {type(err).__name__}: {err}") from err

    engine = DetectionEngine(behaviours, on_detect=_detection_callback(hass))

    # Whole-domain subscriptions need every state change, so we listen to all
    # entities and filter through the subscription model (cheap dict/set
    # lookups) instead of a fixed entity list that would miss new entities.
    # The model lives on the runtime so mutes can swap it without a reload.
    subscriptions = Subscriptions(
        entry.data[CONF_ENTITIES], entry.data.get(CONF_EXCLUDE_ENTITIES, ())
    )
    from homeassistant.helpers.entity_registry import (  # function-local: purity guard
        async_get as async_get_entity_registry,
    )

    is_noise = registry_is_noise(async_get_entity_registry(hass))
    # Analysis log: small persisted ring buffer shown under the activity
    # feed. Loaded leniently (never fails setup — it is decoration, not data).
    analysis_log_store = AnalysisLogStore(hass.config.path(CONFIG_DIR, ANALYSIS_LOG_FILE))
    runtime = Phil9000Runtime(
        engine=engine,
        store=store,
        subscriptions=subscriptions,
        is_noise=is_noise,
        options_snapshot=dict(entry.options),
        analysis_log=await hass.async_add_executor_job(analysis_log_store.load),
        analysis_log_store=analysis_log_store,
    )
    hass.data[DOMAIN][entry.entry_id] = runtime

    unsub = hass.bus.async_listen(EVENT_STATE_CHANGED, _event_handler(hass, entry, runtime))
    entry.async_on_unload(unsub)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    behaviour_id_schema = vol.Schema({vol.Required(ATTR_BEHAVIOUR_ID): str})
    hass.services.async_register(
        DOMAIN,
        SERVICE_CONFIRM_BEHAVIOUR,
        _status_service_handler(hass, entry, BehaviourStatus.CONFIRMED),
        schema=behaviour_id_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISMISS_BEHAVIOUR,
        _status_service_handler(hass, entry, BehaviourStatus.DISMISSED),
        schema=behaviour_id_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_ANALYSIS,
        _run_analysis_service_handler(hass, entry),
        schema=vol.Schema({}),
    )

    if entry.options.get(OPTION_ANALYSIS_ENABLED, False):
        from homeassistant.core import callback  # function-local: purity guard
        from homeassistant.helpers.event import (  # function-local: purity guard
            async_track_time_change,
        )

        @callback
        def _nightly(now) -> None:  # noqa: ANN001
            hass.async_create_task(_async_nightly_analysis(hass, entry, runtime))

        entry.async_on_unload(async_track_time_change(hass, _nightly, hour=3, minute=30, second=0))
        _LOGGER.debug("PHIL9000 nightly analysis scheduled (03:30)")

    await _async_ensure_ui(hass)
    if not hass.data.get(UI_REGISTERED_FLAG):
        # http/frontend are not up yet (early startup): register the panel
        # as soon as they load. Unsubscribed when the entry unloads.
        from homeassistant.const import (  # function-local: purity guard
            EVENT_COMPONENT_LOADED,
        )
        from homeassistant.core import callback  # function-local: purity guard

        @callback
        def _component_loaded(event: Event) -> None:
            if event.data.get("component") in ("frontend", "http"):
                hass.async_create_task(_async_ensure_ui(hass))

        entry.async_on_unload(hass.bus.async_listen(EVENT_COMPONENT_LOADED, _component_loaded))

    observed = subscriptions.expand(hass.states.async_entity_ids(), is_noise)
    _LOGGER.debug(
        "PHIL9000 observing %d entities (%d domains), %d behaviours loaded",
        len(observed),
        len(subscriptions.domains),
        len(behaviours),
    )
    return True


async def _async_ensure_ui(hass: HomeAssistant) -> bool:
    """Serve the panel JS and add the sidebar panel (once per HA run)."""
    if hass.data.get(UI_REGISTERED_FLAG):
        return True
    if "http" not in hass.config.components or "frontend" not in hass.config.components:
        return False

    from homeassistant.components import (  # function-local: purity guard
        panel_custom,
    )
    from homeassistant.components.http import StaticPathConfig  # local: purity

    try:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    STATIC_URL_PATH,
                    os.path.join(os.path.dirname(__file__), "frontend"),
                    cache_headers=False,
                )
            ]
        )
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name=PANEL_WEBCOMPONENT,
            frontend_url_path=PANEL_URL_PATH,
            sidebar_title="PHIL9000",
            sidebar_icon="mdi:eye-circle",
            module_url=f"{STATIC_URL_PATH}/panel.js",
            require_admin=False,
        )
    except Exception:  # noqa: BLE001 — the integration works headless without UI
        _LOGGER.exception("Failed to register the PHIL9000 sidebar panel")
        return False
    hass.data[UI_REGISTERED_FLAG] = True
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: Phil9000Runtime | None = hass.data[DOMAIN].pop(entry.entry_id, None)
    if runtime is not None:
        await _async_save(hass, runtime)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Pick up config changes with as little disruption as possible.

    Entity selection (domains, explicit ids, mutes) lives entirely in the
    runtime's Subscriptions object, so data-only changes are swapped in
    place: no reload, live panel subscriptions keep streaming, and a mute
    takes effect on the next event. Options changes can alter the nightly
    analysis schedule, so those still reload the entry.
    """
    runtime: Phil9000Runtime | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is not None and dict(entry.options) == runtime.options_snapshot:
        runtime.subscriptions = Subscriptions(
            entry.data.get(CONF_ENTITIES, []), entry.data.get(CONF_EXCLUDE_ENTITIES, ())
        )
        return
    # Fire-and-forget per HA's update-listener pattern: awaiting a reload
    # from inside the listener can re-enter setup while the entry is being
    # updated. The task is tracked, so tests block on it via block_till_done.
    hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))


# -- detection → events & actions ---------------------------------------


def _detection_callback(hass: HomeAssistant):  # noqa: ANN202
    """Engine hook: fire the domain event, then run the behaviour's actions."""

    def on_detect(behaviour: Behaviour, matched: list[Observation]) -> None:
        hass.bus.async_fire(
            EVENT_BEHAVIOUR_DETECTED,
            {
                ATTR_BEHAVIOUR_ID: behaviour.id,
                ATTR_BEHAVIOUR_NAME: behaviour.name,
                ATTR_STATUS: behaviour.status.value,
                ATTR_CONFIDENCE: round(behaviour.confidence, 3),
                ATTR_OCCURRENCES: behaviour.occurrences,
                ATTR_MATCHED: [obs.to_dict() for obs in matched],
                ATTR_ACTIONS: [action.to_dict() for action in behaviour.actions],
            },
        )
        if behaviour.actions:
            hass.async_create_task(_execute_actions(hass, behaviour))

    return on_detect


async def _execute_actions(hass: HomeAssistant, behaviour: Behaviour) -> None:
    """Run a confirmed behaviour's actions as HA service calls, in order.

    Each action is isolated: one failing service call is logged and does not
    prevent the remaining actions from running.
    """
    for action in behaviour.actions:
        try:
            await hass.services.async_call(
                action.domain,
                action.service,
                dict(action.data),
                target={"entity_id": action.entity_id} if action.entity_id else None,
                blocking=True,
            )
            _LOGGER.info(
                "Executed %s.%s for behaviour %r",
                action.domain,
                action.service,
                behaviour.name,
            )
        except Exception:  # noqa: BLE001 — keep the other actions going
            _LOGGER.exception(
                "Action %s.%s for behaviour %r failed",
                action.domain,
                action.service,
                behaviour.name,
            )


# -- observation → engine → suggestions ----------------------------------


def _event_handler(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: Phil9000Runtime,
):  # noqa: ANN202
    """Translate state_changed events into Observations for the engine."""
    from homeassistant.core import callback  # function-local: purity guard

    engine = runtime.engine

    @callback
    def handle_event(event: Event) -> None:
        new_state = event.data.get("new_state")
        entity_id = event.data["entity_id"]
        if new_state is None:  # entity removed
            return
        old_state = event.data.get("old_state")
        if old_state is not None and old_state.state == new_state.state:
            # Attribute-only change: never matches a step, and it would flood
            # the activity feed (chatty sensors update attributes constantly).
            return
        if not runtime.subscriptions.matches(entity_id, runtime.is_noise):
            return
        observation = Observation(
            entity_id=entity_id,
            new_state=new_state.state,
            old_state=old_state.state if old_state else None,
            timestamp=new_state.last_changed,
        )
        _publish_activity(runtime, observation)
        if not engine.involves(entity_id):
            return  # no sequence involves this entity: nothing can change
        before = {bid: b.occurrences for bid, b in engine.behaviours.items()}
        for detection in engine.observe(observation):
            trail = " → ".join(f"{o.entity_id}={o.new_state}" for o in detection.matched)
            _LOGGER.info(
                "Detected %s (confidence %.0f%%): %s",
                detection.behaviour.name,
                detection.behaviour.confidence * 100,
                trail,
            )
            runtime.last_detections[detection.behaviour.id] = {
                "at": detection.matched[-1].timestamp.isoformat(),
                "observations": [obs.to_dict() for obs in detection.matched],
            }

        newly_matched = [
            engine.behaviours[bid]
            for bid, count in before.items()
            if engine.behaviours[bid].occurrences > count
        ]
        if newly_matched:
            # The engine tallies occurrences for every status (including
            # matches suppressed by cooldown); persist whenever counters moved.
            runtime.schedule_save(hass)
            for behaviour in newly_matched:
                if (
                    behaviour.status is BehaviourStatus.CANDIDATE
                    and behaviour.id not in runtime.suggested
                ):
                    runtime.suggested.add(behaviour.id)
                    hass.async_create_task(_notify_suggestion(hass, entry, behaviour))

    return handle_event


def _publish_activity(runtime: Phil9000Runtime, observation: Observation) -> None:
    """Push a live feed row to the panel's activity subscribers (best effort)."""
    if not runtime.activity_subscribers:
        return
    if is_recovery({"old_state": observation.old_state}):
        return  # entity coming back online, not household activity
    row = {
        "entity_id": observation.entity_id,
        "at": observation.timestamp.isoformat(),
        "old_state": observation.old_state,
        "new_state": observation.new_state,
    }
    for subscriber in list(runtime.activity_subscribers):
        try:
            subscriber(row)
        except Exception:  # noqa: BLE001 — a stale connection must not break observing
            _LOGGER.debug("PHIL9000 activity subscriber failed", exc_info=True)


async def _notify_suggestion(hass: HomeAssistant, entry: ConfigEntry, behaviour: Behaviour) -> None:
    """Nudge the user about a candidate pattern (secondary channel to UI)."""
    title = "PHIL9000 noticed a pattern"
    steps = " → ".join(f"{s.entity_id} = {s.to_state}" for s in behaviour.steps)
    message = (
        f"“{behaviour.name}” ({steps}). Seen {behaviour.occurrences} time"
        f"{'s' if behaviour.occurrences != 1 else ''}, confidence "
        f"{behaviour.confidence:.0%}. Confirm it with the "
        f"{DOMAIN}.{SERVICE_CONFIRM_BEHAVIOUR} service, or dismiss it with "
        f"{DOMAIN}.{SERVICE_DISMISS_BEHAVIOUR} (behaviour_id: {behaviour.id})."
    )
    await notifier.async_notify(hass, entry, title, message)


# -- confirm / dismiss services -------------------------------------------


def _status_service_handler(hass: HomeAssistant, entry: ConfigEntry, status: BehaviourStatus):  # noqa: ANN202
    """Build a confirm/dismiss service handler for this config entry."""

    async def handle(call: ServiceCall) -> None:
        from homeassistant.exceptions import (  # function-local: purity guard
            ServiceValidationError,
            Unauthorized,
        )

        # Confirm/dismiss decide whether PHIL9000 acts; run_analysis rewrites
        # candidate definitions. Actions execute with the integration's
        # authority, so only admin users may amend. System calls (no user)
        # stay allowed for automations.
        user_id = call.context.user_id
        if user_id is not None:
            user = await hass.auth.async_get_user(user_id)
            if user is None or not user.is_admin:
                _LOGGER.warning("Rejected %s call by non-admin user %s", DOMAIN, user_id)
                raise Unauthorized(call.context)
        runtime: Phil9000Runtime | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if runtime is None:
            raise ServiceValidationError("PHIL9000 is not loaded")
        behaviour_id = call.data[ATTR_BEHAVIOUR_ID]
        try:
            behaviour = await runtime.async_set_status(hass, behaviour_id, status)
        except ValueError as err:
            raise ServiceValidationError(
                f"Unknown behaviour id {behaviour_id!r}. Behaviour ids are listed "
                f"in {hass.config.path(CONFIG_DIR, BEHAVIOURS_FILE)}."
            ) from err
        _LOGGER.info("Behaviour %r is now %s", behaviour.name, behaviour.status.value)

    return handle


# -- LLM analysis (optional, Milestone 5) -------------------------------------


def _run_analysis_service_handler(hass: HomeAssistant, entry: ConfigEntry):  # noqa: ANN202
    """phil9000.run_analysis: on-demand history analysis via the conversation agent."""

    async def handle(call: ServiceCall) -> None:  # noqa: ARG001
        from homeassistant.exceptions import (  # function-local: purity guard
            ServiceValidationError,
            Unauthorized,
        )

        if call.context.user_id is not None:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                _LOGGER.warning(
                    "Rejected %s call by non-admin user %s", DOMAIN, call.context.user_id
                )
                raise Unauthorized(call.context)
        runtime: Phil9000Runtime | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if runtime is None:
            raise ServiceValidationError("PHIL9000 is not loaded")
        try:
            await analysis.async_run_analysis(hass, entry, runtime, trigger="service")
        except analysis.AnalysisUnavailableError as err:
            raise ServiceValidationError(str(err)) from err

    return handle


async def _async_nightly_analysis(
    hass: HomeAssistant, entry: ConfigEntry, runtime: Phil9000Runtime
) -> None:
    try:
        await analysis.async_run_analysis(hass, entry, runtime, trigger="nightly")
    except analysis.AnalysisUnavailableError as err:
        _LOGGER.warning("Scheduled PHIL9000 analysis skipped: %s", err)


# -- persistence ------------------------------------------------------------


async def _async_save(hass: HomeAssistant, runtime: Phil9000Runtime) -> None:
    try:
        await hass.async_add_executor_job(
            runtime.store.save, list(runtime.engine.behaviours.values())
        )
    except Exception:  # noqa: BLE001 — persistence must not take the loop down
        _LOGGER.exception("Failed to persist PHIL9000 behaviours")
