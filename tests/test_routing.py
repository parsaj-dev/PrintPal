"""Tests for opt-in smart routing."""
from __future__ import annotations

from printpal.config import Config
from printpal.detect import KIND_DOCUMENT, KIND_LABEL
from printpal import routing


def _cfg(**kw):
    c = Config()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_routing_off_always_selected_printer():
    c = _cfg(printer="Sel", thermal_printer="Thermal", paper_printer="Paper")
    assert routing.printer_for(KIND_LABEL, c, routing_enabled=False).printer == "Sel"
    assert routing.printer_for(KIND_DOCUMENT, c, routing_enabled=False).printer == "Sel"


def test_routing_on_splits_by_kind():
    c = _cfg(printer="Sel", thermal_printer="Thermal", paper_printer="Paper")
    assert routing.printer_for(KIND_LABEL, c, True).printer == "Thermal"
    assert routing.printer_for(KIND_DOCUMENT, c, True).printer == "Paper"


def test_routing_falls_back_to_selected_when_unset():
    c = _cfg(printer="Sel", thermal_printer="", paper_printer="")
    assert routing.printer_for(KIND_LABEL, c, True).printer == "Sel"
    assert routing.printer_for(KIND_DOCUMENT, c, True).printer == "Sel"


def test_routing_available_only_when_targets_differ():
    assert routing.routing_available(_cfg(thermal_printer="A", paper_printer="B"))
    assert not routing.routing_available(_cfg(printer="A", thermal_printer="", paper_printer=""))
