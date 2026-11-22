"""Round-trip tests for models and the YAML behaviour and analysis-log stores."""

from datetime import time
from pathlib import Path

import pytest

from custom_components.phil9000.models import (
    Behaviour,
    BehaviourAction,
    BehaviourStatus,
    BehaviourStep,
)
from custom_components.phil9000.seed import end_of_work_day
from custom_components.phil9000.yaml_store import AnalysisLogStore, BehaviourStore


def make_behaviour() -> Behaviour:
    return Behaviour(
        id="weekend_bake",
        name="Weekend baking",
        description="Kitchen activity on weekend mornings",
        steps=[
            BehaviourStep("binary_sensor.kitchen_presence", "on"),
            BehaviourStep("switch.oven_plug", "on", max_gap_seconds=900),
        ],
        actions=[
            BehaviourAction("tts", "speak", "tts.hal", {"message": "Baking again?"}),
        ],
        weekdays={6, 7},
        time_start=time(7, 0),
        time_end=time(12, 0),
        confidence=0.4,
        occurrences=2,
        status=BehaviourStatus.CANDIDATE,
        cooldown_seconds=7200,
    )


class TestModelRoundTrip:
    def test_to_from_dict(self) -> None:
        behaviour = make_behaviour()
        restored = Behaviour.from_dict(behaviour.to_dict())
        assert restored == behaviour

    def test_generated_id_when_empty(self) -> None:
        behaviour = Behaviour(id="", name="anon")
        assert behaviour.id
        assert len(behaviour.id) == 12

    def test_status_accepts_string(self) -> None:
        behaviour = Behaviour(id="x", name="x", status="confirmed")  # type: ignore[arg-type]
        assert behaviour.status is BehaviourStatus.CONFIRMED


class TestYamlStore:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        store = BehaviourStore(tmp_path / "behaviours.yaml")
        assert store.load() == []

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        store = BehaviourStore(tmp_path / "sub" / "behaviours.yaml")
        behaviours = [make_behaviour(), end_of_work_day()]
        store.save(behaviours)
        assert store.load() == behaviours

    def test_upsert_replaces_existing(self, tmp_path: Path) -> None:
        store = BehaviourStore(tmp_path / "behaviours.yaml")
        behaviour = make_behaviour()
        store.upsert(behaviour)
        behaviour.confidence = 0.9
        store.upsert(behaviour)
        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0].confidence == 0.9

    def test_remove(self, tmp_path: Path) -> None:
        store = BehaviourStore(tmp_path / "behaviours.yaml")
        store.save([make_behaviour(), end_of_work_day()])
        loaded = store.remove("weekend_bake")
        assert [b.id for b in loaded] == ["end_of_work_day"]

    def test_duplicate_ids_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "behaviours.yaml"
        path.write_text(
            "version: 1\nbehaviours:\n  - id: dup\n    name: One\n  - id: dup\n    name: Two\n",
            encoding="utf-8",
        )
        store = BehaviourStore(path)
        with pytest.raises(ValueError, match="duplicate behaviour id"):
            store.load()

    def test_yaml_is_human_editable(self, tmp_path: Path) -> None:
        store = BehaviourStore(tmp_path / "behaviours.yaml")
        store.save([end_of_work_day()])
        text = (tmp_path / "behaviours.yaml").read_text()
        assert "End of work day" in text
        assert "binary_sensor.office_presence" in text


class TestAnalysisLogStore:
    def test_missing_file_starts_empty(self, tmp_path: Path) -> None:
        assert AnalysisLogStore(tmp_path / "analysis_log.yaml").load() == []

    def test_round_trip_preserves_order(self, tmp_path: Path) -> None:
        store = AnalysisLogStore(tmp_path / "analysis_log.yaml")
        entries = [
            {"at": "2026-11-22T03:30:00+00:00", "level": "info", "message": "Run started"},
            {"at": "2026-11-22T03:30:12+00:00", "level": "error", "message": "Run failed"},
        ]
        store.save(entries)
        assert store.load() == entries

    def test_damaged_file_is_tolerated(self, tmp_path: Path) -> None:
        path = tmp_path / "analysis_log.yaml"
        path.write_text("{{{not yaml", encoding="utf-8")
        assert AnalysisLogStore(path).load() == []

    def test_junk_entries_are_dropped(self, tmp_path: Path) -> None:
        store = AnalysisLogStore(tmp_path / "analysis_log.yaml")
        store.save([{"at": "x", "level": "info", "message": "ok"}])  # type: ignore[list-item]
        path = tmp_path / "analysis_log.yaml"
        path.write_text(
            "runs:\n- at: 2026-11-22T03:30:00+00:00\n  level: info\n  message: ok\n- junk\n",
            encoding="utf-8",
        )
        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0]["message"] == "ok"
