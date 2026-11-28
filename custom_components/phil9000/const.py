"""Constants for PHIL9000.

Pure module — must not import Home Assistant (guarded by tests/test_purity.py).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DOMAIN = "phil9000"

# The integration's version, straight from the manifest: stamping it on the
# panel JS URL (?v=…) means a browser can never keep serving a panel from a
# previous release after an update.
VERSION = json.loads((Path(__file__).parent / "manifest.json").read_text())["version"]

EVENT_BEHAVIOUR_DETECTED = f"{DOMAIN}_behaviour_detected"

# Services
SERVICE_CONFIRM_BEHAVIOUR = "confirm_behaviour"
SERVICE_DISMISS_BEHAVIOUR = "dismiss_behaviour"
SERVICE_RUN_ANALYSIS = "run_analysis"

# Entity domains worth observing (config-flow picker). Chosen to cover
# presence, activity and controllable-device signals while leaving out
# system/utility domains that would bury the list (update, firmware, sun...).
OBSERVABLE_DOMAINS = frozenset(
    {
        "appliance",
        "binary_sensor",
        "climate",
        "cover",
        "device_tracker",
        "fan",
        "humidifier",
        "input_boolean",
        "input_select",
        "light",
        "lock",
        "media_player",
        "person",
        "sensor",
        "switch",
    }
)

# Preselected in the config flow: the signals most households want first.
DEFAULT_OBSERVABLE_DOMAINS = frozenset(
    {"binary_sensor", "device_tracker", "light", "person", "switch"}
)

# Human labels for the domain picker (value -> label).
OBSERVABLE_DOMAIN_LABELS = {
    "appliance": "Appliances",
    "binary_sensor": "Binary sensors (motion, doors, presence)",
    "climate": "Climate",
    "cover": "Covers (blinds, garage)",
    "device_tracker": "Device trackers (phones, tags)",
    "fan": "Fans",
    "humidifier": "Humidifiers",
    "input_boolean": "Helpers (toggles)",
    "input_select": "Helpers (select boxes)",
    "light": "Lights",
    "lock": "Locks",
    "media_player": "Media players",
    "person": "People",
    "sensor": "Sensors (temperature, power, ...)",
    "switch": "Switches",
}

_LABEL_HINT_RE = re.compile(r"^(.*?)\s*\(([^)]*)\)$")


def domain_label_parts(domain: str) -> tuple[str, str]:
    """Split a domain label into display text and its parenthesized hint.

    ``"Binary sensors (motion, doors, presence)"`` → ``("Binary sensors",
    "motion, doors, presence")``. Compact labels suit checklists; the hint
    belongs in a tooltip. Labels without a bracketed part return it whole
    with an empty hint.
    """
    full = OBSERVABLE_DOMAIN_LABELS.get(domain, domain)
    match = _LABEL_HINT_RE.match(full)
    return (match.group(1), match.group(2)) if match else (full, "")


# Config entry data
CONF_ENTITIES = "entities"
# Entry data: domains and/or entity ids to observe; excluded drops entities
# from whole-domain subscriptions ("never observe these").
CONF_EXCLUDE_ENTITIES = "exclude_entities"

# Config entry options
OPTION_AGENT_ID = "agent_id"
OPTION_NOTIFY_ENTITY = "notify_entity"
OPTION_COOLDOWN_DEFAULT = "cooldown_default"
OPTION_ANALYSIS_ENABLED = "analysis_enabled"
OPTION_ANALYSIS_DAYS = "analysis_days"
# Full override of the LLM prompt template ({summary}, {known}, {cap}).
# Blank = the built-in template in analyser.py.
OPTION_CUSTOM_PROMPT = "custom_prompt"

DEFAULT_COOLDOWN_DEFAULT = 3600

# Behaviour definitions live in <HA config>/phil9000/behaviours.yaml
CONFIG_DIR = DOMAIN
BEHAVIOURS_FILE = "behaviours.yaml"
# Ring buffer of recent analysis runs shown in the panel (newest last).
ANALYSIS_LOG_FILE = "analysis_log.yaml"
ANALYSIS_LOG_LIMIT = 50
# The recording in progress (triggers/actions/notes/proposal), so an HA
# restart mid-recording does not lose the user's demonstration.
RECORDING_FILE = "recording.yaml"
# Occurrence-counter saves are debounced: a noisy candidate can bump
# counters on every match, and behaviours.yaml need not rewrite that often.
SAVE_DEBOUNCE_SECONDS = 30

# hass.data flag: static assets + sidebar panel registered this run
UI_REGISTERED_FLAG = f"{DOMAIN}_ui_registered"
# Static URL for the panel JS (served from custom_components/phil9000/frontend)
STATIC_URL_PATH = f"/{DOMAIN}_static"
PANEL_URL_PATH = DOMAIN
PANEL_WEBCOMPONENT = "phil9000-panel"

# phil9000_behaviour_detected event payload keys
ATTR_BEHAVIOUR_ID = "behaviour_id"
ATTR_BEHAVIOUR_NAME = "name"
ATTR_STATUS = "status"
ATTR_CONFIDENCE = "confidence"
ATTR_OCCURRENCES = "occurrences"
ATTR_MATCHED = "matched"
ATTR_ACTIONS = "actions"
