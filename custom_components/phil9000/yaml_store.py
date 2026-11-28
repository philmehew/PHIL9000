"""YAML persistence for behaviours and the analysis run log.

Per the MVP plan, PHIL9000's own storage is limited to behaviours (the
observation data lives in HA's recorder). YAML in the config directory is
user-editable and versionable; writes are atomic.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .models import Behaviour
from .recording import RecordingSession

_LOGGER = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


class BehaviourStore:
    """Loads and saves a list of Behaviours from a single YAML file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- reading -------------------------------------------------------

    def load(self) -> list[Behaviour]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            raw: Any = yaml.safe_load(fh) or {}
        entries = raw.get("behaviours", [])
        behaviours: list[Behaviour] = []
        seen_ids: set[str] = set()
        for entry in entries:
            behaviour = Behaviour.from_dict(entry)
            if behaviour.id in seen_ids:
                raise ValueError(f"duplicate behaviour id: {behaviour.id}")
            seen_ids.add(behaviour.id)
            behaviours.append(behaviour)
        return behaviours

    # -- writing -------------------------------------------------------

    def save(self, behaviours: list[Behaviour]) -> None:
        payload = {
            "version": _SCHEMA_VERSION,
            "behaviours": [b.to_dict() for b in behaviours],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # -- convenience ---------------------------------------------------

    def upsert(self, behaviour: Behaviour) -> list[Behaviour]:
        behaviours = self.load()
        for index, existing in enumerate(behaviours):
            if existing.id == behaviour.id:
                behaviours[index] = behaviour
                break
        else:
            behaviours.append(behaviour)
        self.save(behaviours)
        return behaviours

    def remove(self, behaviour_id: str) -> list[Behaviour]:
        behaviours = [b for b in self.load() if b.id != behaviour_id]
        self.save(behaviours)
        return behaviours


class RecordingStore:
    """Loads and saves the recording session (atomic write, lenient load).

    A recording in progress is user effort, so the write happens on every
    mutation; but a damaged file is an empty session, never a setup failure —
    the patterns that matter live in behaviours.yaml.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> RecordingSession:
        if not self.path.exists():
            return RecordingSession()
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                raw: Any = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            _LOGGER.warning("PHIL9000 ignoring unreadable recording file %s", self.path)
            return RecordingSession()
        recording = raw.get("recording")
        return RecordingSession.from_dict(recording if isinstance(recording, dict) else {})

    def save(self, session: RecordingSession) -> None:
        payload = {"version": _SCHEMA_VERSION, "recording": session.to_dict()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


class EventRingStore:
    """Loads and saves the rolling buffer of event-entity firings (plain rows).

    Lenient like the analysis log: this is history for the feed and the LLM
    digest, not user data — a damaged file starts the ring empty.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                raw: Any = yaml.safe_load(fh) or {}
            events = raw.get("events", [])
            return [e for e in events if isinstance(e, dict) and e.get("at") and e.get("entity_id")]
        except (OSError, yaml.YAMLError):
            _LOGGER.warning("PHIL9000 ignoring unreadable events ring %s", self.path)
            return []

    def save(self, rows: list[dict[str, Any]]) -> None:
        payload = {"version": _SCHEMA_VERSION, "events": rows}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


class AnalysisLogStore:
    """Loads and saves the recent analysis runs (plain dicts).

    More forgiving than the behaviour store: the log is decoration, not
    data — a missing or damaged file simply starts the buffer empty.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                raw: Any = yaml.safe_load(fh) or {}
            entries = raw.get("runs", [])
            return [e for e in entries if isinstance(e, dict) and e.get("at")]
        except (OSError, yaml.YAMLError):
            _LOGGER.warning("PHIL9000 ignoring unreadable analysis log %s", self.path)
            return []

    def save(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.safe_dump({"runs": entries}, fh, sort_keys=False, allow_unicode=True)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
