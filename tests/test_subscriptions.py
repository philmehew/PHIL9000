"""Unit tests for the pure subscription model (no Home Assistant needed)."""

from __future__ import annotations

from custom_components.phil9000.subscriptions import (
    Subscriptions,
    registry_is_noise,
    split_specs,
)


class _FakeEntry:
    def __init__(self, disabled_by=None, hidden_by=None, entity_category=None):
        self.disabled_by = disabled_by
        self.hidden_by = hidden_by
        self.entity_category = entity_category


class _FakeRegistry:
    def __init__(self, entries: dict[str, _FakeEntry]):
        self._entries = entries

    def async_get(self, entity_id: str):
        return self._entries.get(entity_id)


def test_split_specs_separates_domains_from_entity_ids() -> None:
    domains, entity_ids = split_specs(["binary_sensor", "light.desk", "switch", "person"])
    assert domains == {"binary_sensor", "switch", "person"}
    assert entity_ids == {"light.desk"}


def test_exact_ids_match_and_exclusion_wins() -> None:
    subs = Subscriptions(["light.desk"], exclude=["light.desk"])
    assert not subs.matches("light.desk")

    subs = Subscriptions(["light.desk"])
    assert subs.matches("light.desk")
    assert not subs.matches("light.other")


def test_domain_membership_with_and_without_noise() -> None:
    subs = Subscriptions(["binary_sensor"])
    assert subs.matches("binary_sensor.anything")
    assert not subs.matches("binary_sensor.noisy", is_noise=lambda eid: eid.endswith("noisy"))
    assert not subs.matches("switch.other")


def test_explicit_id_is_kept_even_if_noisy() -> None:
    # A deliberately picked entity stays observed even when the registry
    # marks it diagnostic or hidden.
    subs = Subscriptions(["sensor.firmware"])
    assert subs.matches("sensor.firmware", is_noise=lambda eid: True)


def test_expand_filters_and_sorts() -> None:
    subs = Subscriptions(["switch"], exclude=["switch.excluded"])
    universe = [
        "switch.b",
        "switch.a",
        "switch.excluded",
        "light.c",
        "switch.noisy",
    ]
    out = subs.expand(universe, is_noise=lambda eid: eid == "switch.noisy")
    assert out == ["switch.a", "switch.b"]


def test_registry_is_noise() -> None:
    registry = _FakeRegistry(
        {
            "sensor.disabled": _FakeEntry(disabled_by="user"),
            "sensor.hidden": _FakeEntry(hidden_by="integration"),
            "sensor.diag": _FakeEntry(entity_category="diagnostic"),
            "sensor.config": _FakeEntry(entity_category="config"),
            "sensor.fine": _FakeEntry(),
        }
    )
    is_noise = registry_is_noise(registry)
    assert is_noise("sensor.disabled")
    assert is_noise("sensor.hidden")
    assert is_noise("sensor.diag")
    assert is_noise("sensor.config")
    assert not is_noise("sensor.fine")
    assert not is_noise("sensor.unregistered")
