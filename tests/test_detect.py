"""Tests for the detection engine using synthetic event streams."""

from datetime import datetime, time

import pytest

from custom_components.hal1976.detect import DetectionEngine, _in_time_window
from custom_components.hal1976.models import Behaviour, BehaviourStatus, BehaviourStep, Observation
from custom_components.hal1976.seed import end_of_work_day

MON_17_30 = datetime(2026, 11, 23, 17, 30)  # a Monday
MON_17_32 = MON_17_30.replace(minute=32)
MON_17_33 = MON_17_30.replace(minute=33)
MON_17_45 = MON_17_30.replace(minute=45)
MON_18_00 = datetime(2026, 11, 23, 18, 0)
MON_18_01 = datetime(2026, 11, 23, 18, 1)
MON_18_35 = datetime(2026, 11, 23, 18, 35)
MON_18_36 = datetime(2026, 11, 23, 18, 36)
MON_18_30 = datetime(2026, 11, 23, 18, 30)
MON_18_31 = datetime(2026, 11, 23, 18, 31)
MON_17_29 = MON_17_30.replace(minute=29)
MON_17_31 = MON_17_30.replace(minute=31)
OFFICE = "binary_sensor.office_presence"
KITCHEN = "binary_sensor.kitchen_presence"


def obs(entity: str, state: str, at: datetime) -> Observation:
    return Observation(entity_id=entity, new_state=state, timestamp=at)


@pytest.fixture
def confirmed_behaviour() -> Behaviour:
    return end_of_work_day(BehaviourStatus.CONFIRMED)


class TestHappyPath:
    def test_detects_end_of_work_day(self, confirmed_behaviour: Behaviour) -> None:
        engine = DetectionEngine([confirmed_behaviour])
        detections = engine.observe_all(
            [
                obs(OFFICE, "off", MON_17_30),
                obs(KITCHEN, "on", MON_17_32),
            ]
        )
        assert len(detections) == 1
        assert detections[0].behaviour.name == "End of work day"
        assert len(detections[0].matched) == 2
        assert confirmed_behaviour.occurrences == 1
        assert confirmed_behaviour.confidence == pytest.approx(0.5)

    def test_on_detect_callback(self, confirmed_behaviour: Behaviour) -> None:
        seen = []
        engine = DetectionEngine(
            [confirmed_behaviour], on_detect=lambda b, m: seen.append((b, m))
        )
        engine.observe_all([obs(OFFICE, "off", MON_17_30), obs(KITCHEN, "on", MON_17_33)])
        assert len(seen) == 1
        assert seen[0][0] is confirmed_behaviour

    def test_unrelated_events_ignored(self, confirmed_behaviour: Behaviour) -> None:
        engine = DetectionEngine([confirmed_behaviour])
        detections = engine.observe_all(
            [
                obs("light.lounge", "on", MON_17_29),
                obs(OFFICE, "off", MON_17_30),
                obs("media_player.tv", "playing", MON_17_31),
                obs(KITCHEN, "on", MON_17_32),
            ]
        )
        assert len(detections) == 1


class TestNegativeCases:
    def test_kitchen_only_never_fires(self, confirmed_behaviour: Behaviour) -> None:
        engine = DetectionEngine([confirmed_behaviour])
        assert engine.observe_all([obs(KITCHEN, "on", MON_17_30)]) == []

    def test_wrong_state_never_fires(self, confirmed_behaviour: Behaviour) -> None:
        engine = DetectionEngine([confirmed_behaviour])
        detections = engine.observe_all(
            [obs(OFFICE, "off", MON_17_30), obs(KITCHEN, "off", MON_17_32)]
        )
        assert detections == []

    def test_gap_exceeded_breaks_partial(self, confirmed_behaviour: Behaviour) -> None:
        engine = DetectionEngine([confirmed_behaviour])
        detections = engine.observe_all(
            [obs(OFFICE, "off", MON_17_30), obs(KITCHEN, "on", MON_17_45)]
        )
        assert detections == []

    def test_office_back_on_breaks_partial(self, confirmed_behaviour: Behaviour) -> None:
        """User steps out of the office briefly; the pattern should break."""
        engine = DetectionEngine([confirmed_behaviour])
        detections = engine.observe_all(
            [
                obs(OFFICE, "off", MON_17_30),
                obs(OFFICE, "on", MON_17_31),
                obs(KITCHEN, "on", MON_17_33),
            ]
        )
        assert detections == []

    def test_weekend_suppressed(self, confirmed_behaviour: Behaviour) -> None:
        engine = DetectionEngine([confirmed_behaviour])
        saturday = datetime(2026, 11, 21, 17, 30)
        detections = engine.observe_all(
            [obs(OFFICE, "off", saturday), obs(KITCHEN, "on", saturday.replace(minute=32))]
        )
        assert detections == []

    def test_outside_time_window_suppressed(self, confirmed_behaviour: Behaviour) -> None:
        engine = DetectionEngine([confirmed_behaviour])
        morning = datetime(2026, 11, 23, 10, 30)
        detections = engine.observe_all(
            [obs(OFFICE, "off", morning), obs(KITCHEN, "on", morning.replace(minute=32))]
        )
        assert detections == []


class TestCandidateStatus:
    def test_candidate_accumulates_but_never_fires(self) -> None:
        candidate = end_of_work_day(BehaviourStatus.CANDIDATE)
        engine = DetectionEngine([candidate])
        detections = engine.observe_all(
            [obs(OFFICE, "off", MON_17_30), obs(KITCHEN, "on", MON_17_32)]
        )
        assert detections == []
        assert candidate.occurrences == 1
        assert candidate.status is BehaviourStatus.CANDIDATE


class TestCooldown:
    def test_second_match_suppressed_within_cooldown(
        self, confirmed_behaviour: Behaviour
    ) -> None:
        engine = DetectionEngine([confirmed_behaviour])
        engine.observe_all([obs(OFFICE, "off", MON_17_30), obs(KITCHEN, "on", MON_17_32)])
        detections = engine.observe_all(
            [obs(OFFICE, "off", MON_18_00), obs(KITCHEN, "on", MON_18_01)]
        )
        assert detections == []
        # Occurrence still counted even though suppressed by cooldown.
        assert confirmed_behaviour.occurrences == 2

    def test_re_fires_after_cooldown(self, confirmed_behaviour: Behaviour) -> None:
        confirmed_behaviour.cooldown_seconds = 60
        engine = DetectionEngine([confirmed_behaviour])
        engine.observe_all([obs(OFFICE, "off", MON_17_30), obs(KITCHEN, "on", MON_17_32)])
        detections = engine.observe_all(
            [obs(OFFICE, "off", MON_18_35), obs(KITCHEN, "on", MON_18_36)]
        )
        assert len(detections) == 1


class TestGapExpiredRestart:
    def test_expired_gap_same_event_can_restart(self) -> None:
        """A late first-step event may both expire a stale partial and start fresh."""
        behaviour = Behaviour(
            id="simple",
            name="Presence toggle",
            steps=[
                BehaviourStep(OFFICE, "off"),
                BehaviourStep(KITCHEN, "on", max_gap_seconds=60),
            ],
            status=BehaviourStatus.CONFIRMED,
            cooldown_seconds=0,
        )
        engine = DetectionEngine([behaviour])
        # First partial at 17:00 expires before the next sequence at 18:00.
        engine.observe_all([obs(OFFICE, "off", MON_17_30)])
        detections = engine.observe_all(
            [obs(OFFICE, "off", MON_18_30), obs(KITCHEN, "on", MON_18_31)]
        )
        assert len(detections) == 1


class TestSingleStepBehaviour:
    def test_single_step_fires_immediately(self) -> None:
        behaviour = Behaviour(
            id="bed",
            name="Went to bed",
            steps=[BehaviourStep("binary_sensor.bed_occupied", "on")],
            status=BehaviourStatus.CONFIRMED,
            cooldown_seconds=3600,
        )
        engine = DetectionEngine([behaviour])
        detections = engine.observe_all([obs("binary_sensor.bed_occupied", "on", MON_17_30)])
        assert len(detections) == 1


class TestTimeWindowWrap:
    def test_wrap_midnight(self) -> None:
        assert _in_time_window(time(23, 30), time(22, 0), time(6, 0))
        assert _in_time_window(time(5, 0), time(22, 0), time(6, 0))
        assert not _in_time_window(time(12, 0), time(22, 0), time(6, 0))

    def test_normal_window(self) -> None:
        assert _in_time_window(time(17, 0), time(16, 0), time(19, 0))
        assert not _in_time_window(time(20, 0), time(16, 0), time(19, 0))
        assert _in_time_window(time(20, 0), None, None)
