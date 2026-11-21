"""YAML persistence for behaviour definitions.

Per the MVP plan, HAL3000's own storage is limited to behaviours (the
observation data lives in HA's recorder). YAML in the config directory is
user-editable and versionable; writes are atomic.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .models import Behaviour

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
