"""Constants for HAL1976.

Pure module — must not import Home Assistant (guarded by tests/test_purity.py).
"""

from __future__ import annotations

DOMAIN = "hal1976"

EVENT_BEHAVIOUR_DETECTED = f"{DOMAIN}_behaviour_detected"

# Services
SERVICE_CONFIRM_BEHAVIOUR = "confirm_behaviour"
SERVICE_DISMISS_BEHAVIOUR = "dismiss_behaviour"
SERVICE_RUN_ANALYSIS = "run_analysis"

# Config entry data
CONF_ENTITIES = "entities"

# Config entry options
OPTION_AGENT_ID = "agent_id"
OPTION_NOTIFY_ENTITY = "notify_entity"
OPTION_COOLDOWN_DEFAULT = "cooldown_default"
OPTION_ANALYSIS_ENABLED = "analysis_enabled"
OPTION_ANALYSIS_DAYS = "analysis_days"

DEFAULT_COOLDOWN_DEFAULT = 3600

# Behaviour definitions live in <HA config>/hal1976/behaviours.yaml
CONFIG_DIR = DOMAIN
BEHAVIOURS_FILE = "behaviours.yaml"

# hass.data flag: static assets + sidebar panel registered this run
UI_REGISTERED_FLAG = f"{DOMAIN}_ui_registered"
# Static URL for the panel JS (served from custom_components/hal1976/frontend)
STATIC_URL_PATH = f"/{DOMAIN}_static"
PANEL_URL_PATH = DOMAIN
PANEL_WEBCOMPONENT = "hal1976-panel"

# hal1976_behaviour_detected event payload keys
ATTR_BEHAVIOUR_ID = "behaviour_id"
ATTR_BEHAVIOUR_NAME = "name"
ATTR_STATUS = "status"
ATTR_CONFIDENCE = "confidence"
ATTR_OCCURRENCES = "occurrences"
ATTR_MATCHED = "matched"
ATTR_ACTIONS = "actions"
