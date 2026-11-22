"""Seed behaviours used for tests and the first-run demo."""

from __future__ import annotations

from datetime import time

from .models import Behaviour, BehaviourStatus, BehaviourStep

END_OF_WORK_DAY_ID = "end_of_work_day"


def end_of_work_day(status: BehaviourStatus = BehaviourStatus.CONFIRMED) -> Behaviour:
    """Office presence off → kitchen presence on within 5 min, weekdays 16:00–19:00."""
    return Behaviour(
        id=END_OF_WORK_DAY_ID,
        name="End of work day",
        description=(
            "You left the office and appeared in the kitchen shortly after, "
            "on weekday late afternoons."
        ),
        steps=[
            BehaviourStep("binary_sensor.office_presence", "off"),
            BehaviourStep("binary_sensor.kitchen_presence", "on", max_gap_seconds=300),
        ],
        weekdays={1, 2, 3, 4, 5},
        time_start=time(16, 0),
        time_end=time(19, 0),
        status=status,
        cooldown_seconds=3600,
    )
