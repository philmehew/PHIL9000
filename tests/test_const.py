"""Pure tests for the label helpers in const.py."""

from __future__ import annotations

from custom_components.phil9000.const import (
    OBSERVABLE_DOMAIN_LABELS,
    OBSERVABLE_DOMAINS,
    domain_label_parts,
)


def test_label_splits_bracketed_hint():
    assert domain_label_parts("binary_sensor") == (
        "Binary sensors",
        "motion, doors, presence",
    )
    assert domain_label_parts("sensor") == ("Sensors", "temperature, power, ...")


def test_label_without_hint_passes_through():
    assert domain_label_parts("light") == ("Lights", "")
    assert domain_label_parts("unmapped_domain") == ("unmapped_domain", "")


def test_short_labels_have_no_brackets_left():
    for domain in sorted(OBSERVABLE_DOMAINS):
        label, _hint = domain_label_parts(domain)
        assert "(" not in label and ")" not in label
        assert label == label.strip()
    assert len(OBSERVABLE_DOMAIN_LABELS) == len(OBSERVABLE_DOMAINS)
