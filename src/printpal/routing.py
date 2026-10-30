"""Smart routing by page type -- strictly opt-in.

When the user turns routing on, each page goes to the right device: shipping
labels to the thermal printer, packing slips / A4 sheets to the normal paper
printer. It is **never automatic** -- with routing off (the default) everything
goes to the single selected printer, exactly as before.

A document routed to the paper printer is printed as the *whole page* rather
than the cropped content block: a packing slip or invoice should come out as the
page it is, not cut down to its biggest paragraph.

Pure decision logic; the caller does the actual printing.
"""
from __future__ import annotations

from dataclasses import dataclass

from printpal.config import Config
# Same values as printpal.detect.KIND_*; kept literal so the UI can import
# routing without pulling in OpenCV/NumPy at startup.
KIND_LABEL = "label"
KIND_DOCUMENT = "document"


@dataclass
class RoutingDecision:
    printer: str
    reason: str
    full_page: bool = False     # print the whole page, not the detected crop


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
        return RoutingDecision(paper, "paper (document)", full_page=paper != thermal)
    if kind == KIND_LABEL:
        return RoutingDecision(thermal, "thermal (label)")
    return RoutingDecision(config.printer, "selected printer")


def routing_available(config: Config) -> bool:
    """True only when the two targets differ -- otherwise routing is meaningless."""
    thermal = config.thermal_printer or config.printer
    paper = config.paper_printer or config.printer
    return bool(thermal) and bool(paper) and thermal != paper


def routing_active(config: Config) -> bool:
    """The user has switched routing on *and* it can actually do something."""
    return bool(config.smart_routing) and routing_available(config)
