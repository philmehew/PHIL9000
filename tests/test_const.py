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


def test_display_labels_are_unique():
    """The panel shows only the part before the parentheses (the hint is a
    tooltip), so three "Helpers (…)" labels would render as three identical
    "Helpers" checkboxes. Display labels must tell the domains apart."""
    labels = [domain_label_parts(domain)[0] for domain in OBSERVABLE_DOMAINS]
    assert len(labels) == len(set(labels)), sorted(labels)
