"""Observation subscriptions: whole domains plus exact entity ids.

Pure module — must not import Home Assistant (guarded by tests/test_purity.py).

An entry's ``entities`` list may mix bare domain names (``binary_sensor`` =
observe every current and future entity in that domain) with full entity ids
(``light.desk``). ``exclude`` drops individual entities from domain coverage.

Domain coverage skips "noise" entities (disabled, hidden, diagnostic or
config) — the predicate is injected so this module stays Home Assistant-free;
``registry_is_noise`` adapts a duck-typed entity registry.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

# EntityCategory values the HA frontend treats as utility noise (firmware
# versions, config buttons, ...). Compared as plain strings so this module
# stays Home Assistant-free (EntityCategory is a StrEnum).
NOISE_ENTITY_CATEGORIES = frozenset({"config", "diagnostic"})

_NO_ENTITY_IS_NOISE: Callable[[str], bool] = lambda entity_id: False  # noqa: E731


def split_specs(specs: Iterable[str]) -> tuple[set[str], set[str]]:
    """Split subscription specs into (domains, entity_ids) by presence of '.'."""
    domains: set[str] = set()
    entity_ids: set[str] = set()
    for spec in specs:
        if "." in spec:
            entity_ids.add(spec)
        else:
            domains.add(spec)
    return domains, entity_ids


def registry_is_noise(registry: Any) -> Callable[[str], bool]:
    """Build a noise predicate over an HA entity registry (duck-typed)."""

    def is_noise(entity_id: str) -> bool:
        entry = registry.async_get(entity_id) if registry is not None else None
        if entry is None:
            return False
        if entry.disabled_by is not None or entry.hidden_by is not None:
            return True
        return entry.entity_category in NOISE_ENTITY_CATEGORIES

    return is_noise


class Subscriptions:
    """Decide which entities a state-changed event should observe."""

    __slots__ = ("domains", "entity_ids", "excluded")

    def __init__(self, specs: Iterable[str], exclude: Iterable[str] = ()) -> None:
        self.domains, self.entity_ids = split_specs(specs)
        self.excluded = set(exclude)

    def matches(
        self, entity_id: str, is_noise: Callable[[str], bool] = _NO_ENTITY_IS_NOISE
    ) -> bool:
        """Whether entity_id is observed: exclusion wins, explicit ids are
        always kept (a deliberate pick), domain members skip noise."""
        if entity_id in self.excluded:
            return False
        if entity_id in self.entity_ids:
            return True
        if entity_id.partition(".")[0] not in self.domains:
            return False
        return not is_noise(entity_id)

    def expand(
        self,
        entity_ids: Iterable[str],
        is_noise: Callable[[str], bool] = _NO_ENTITY_IS_NOISE,
    ) -> list[str]:
        """Concrete entity ids (from the given universe) currently observed."""
        return sorted(eid for eid in entity_ids if self.matches(eid, is_noise))
