"""Tiny demo: replay a synthetic evening and show PHIL9000 detecting a behaviour."""

from datetime import datetime

from custom_components.phil9000 import (
    BehaviourAction,
    BehaviourStatus,
    DetectionEngine,
    Observation,
)
from custom_components.phil9000.seed import end_of_work_day


def main() -> None:
    behaviour = end_of_work_day(BehaviourStatus.CONFIRMED)
    behaviour.actions.append(
        BehaviourAction(
            "tts",
            "speak",
            "tts.home_assistant_cloud",
            {"message": "Welcome home — the office is all powered down."},
        )
    )
    engine = DetectionEngine([behaviour])

    evening = [
        ("binary_sensor.office_presence", "off", datetime(2026, 11, 23, 17, 30)),
        ("binary_sensor.kitchen_presence", "on", datetime(2026, 11, 23, 17, 32)),
    ]
    for entity_id, state, at in evening:
        for detection in engine.observe(Observation(entity_id, state, at)):
            print(
                f"Detected: {detection.behaviour.name} "
                f"(confidence {detection.behaviour.confidence:.0%})"
            )
            for action in detection.behaviour.actions:
                print(f"  would call: {action.domain}.{action.service} -> {action.entity_id}")


if __name__ == "__main__":
    main()
