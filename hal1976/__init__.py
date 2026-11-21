"""HAL1976 — learns household behaviour from Home Assistant sensor data.

Core domain logic lives here with no Home Assistant imports; the
custom_components/hal1976 integration is a thin adapter over the recorder,
event bus, Assist, and services.
"""

from .models import (
    Behaviour,
    BehaviourAction,
    BehaviourStatus,
    BehaviourStep,
    Observation,
)
from .detect import Detection, DetectionEngine
from .yaml_store import BehaviourStore

__all__ = [
    "Behaviour",
    "BehaviourAction",
    "BehaviourStatus",
    "BehaviourStep",
    "Observation",
    "Detection",
    "DetectionEngine",
    "BehaviourStore",
]
