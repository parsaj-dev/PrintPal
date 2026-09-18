"""Smart routing by page type -- strictly opt-in.

When the user *manually* enables routing for a job, each page goes to the right
device: 4x6 shipping labels to the thermal printer, packing slips / A4 sheets to
the normal paper printer. This is **never automatic** -- with routing disabled
(the default) everything goes to the single selected printer, exactly as before.

Pure decision logic; the caller does the actual printing.
"""
from __future__ import annotations

from dataclasses import dataclass

from printpal.config import Config
from printpal.detect import KIND_DOCUMENT, KIND_LABEL


@dataclass
class RoutingDecision:
    printer: str
    reason: str


def printer_for(kind: str, config: Config, routing_enabled: bool) -> RoutingDecision:
    """Choose the printer for a page of the given kind.

    With routing off, always the selected printer. With routing on, labels go to
    the thermal printer and documents to the paper printer (each falling back to
    the selected printer when its target isn't configured).
    """
    if not routing_enabled:
        return RoutingDecision(config.printer, "selected printer")

    thermal = config.thermal_printer or config.printer
    paper = config.paper_printer or config.printer

    if kind == KIND_DOCUMENT:
        return RoutingDecision(paper, "paper (document)")
    if kind == KIND_LABEL:
        return RoutingDecision(thermal, "thermal (label)")
    return RoutingDecision(config.printer, "selected printer")


def routing_available(config: Config) -> bool:
    """True only when the two targets differ -- otherwise routing is meaningless."""
    thermal = config.thermal_printer or config.printer
    paper = config.paper_printer or config.printer
    return bool(thermal) and bool(paper) and thermal != paper
