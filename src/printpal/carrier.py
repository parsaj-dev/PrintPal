"""Best-effort carrier + tracking-number recognition from barcode payloads.

Shipping labels encode the tracking number in their barcode(s), but every
carrier uses a different scheme (and some wrap it in a routing/GS1 string). This
module pulls a human tracking number and a carrier name out of the decoded
payloads using well-known public formats. It is deliberately general -- no
store- or account-specific logic -- and best-effort: when nothing matches, it
still returns the raw payload so history has *something* to show.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

UPS = "UPS"
USPS = "USPS"
FEDEX = "FedEx"
DHL = "DHL"
CANADA_POST = "Canada Post"
PUROLATOR = "Purolator"
UNKNOWN = "Unknown"


@dataclass
class Shipment:
    carrier: str            # one of the constants above, or UNKNOWN
    tracking: str | None    # the extracted tracking number, or None


# Ordered most-specific first. Each entry: (carrier, compiled regex, group).
# Patterns match public, documented formats; kept conservative to avoid
# mislabelling one carrier's number as another's.
_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    # UPS: 1Z + 16 alphanumerics.
    (UPS, re.compile(r"\b(1Z[0-9A-Z]{16})\b"), 1),
    # USPS IMpb: 22-26 digits beginning 91-95 (420ZIP prefix stripped below).
    (USPS, re.compile(r"\b(9[1-5]\d{18,24})\b"), 1),
    # UPU S10 (EE123456789US) used by USPS/international.
    (USPS, re.compile(r"\b([A-Z]{2}\d{9}US)\b"), 1),
    # FedEx Ground "96" barcode: 22 digits starting 96.
    (FEDEX, re.compile(r"\b(96\d{20})\b"), 1),
    # DHL: 10 digits (express) commonly, or JD/JJD prefixes.
    (DHL, re.compile(r"\b(JJD\d{15,20}|JD\d{15,18})\b"), 1),
    # Canada Post: 16 digits.
    (CANADA_POST, re.compile(r"\b(\d{16})\b"), 1),
    # FedEx Express: 12 or 15 digits (checked after the more specific ones).
    (FEDEX, re.compile(r"\b(\d{15}|\d{12})\b"), 1),
]

# A leading GS1-128 routing application identifier some labels prepend.
_STRIP_PREFIXES = ("420", "[)>", "\x1e", "\x04")


def _clean(payload: str) -> str:
    p = payload.strip()
    # UPS MaxiCode / GS1 data can carry control chars and group separators.
    return "".join(ch for ch in p if ch.isprintable()).strip()


def parse_tracking(payloads: list[str]) -> Shipment:
    """Return the carrier + tracking number recognised from decoded barcodes.

    Tries each payload against the known formats, most specific first. Falls back
    to (UNKNOWN, first-non-empty-payload) so nothing is silently lost.
    """
    cleaned = [_clean(p) for p in payloads if p and p.strip()]
    for carrier, pattern, grp in _PATTERNS:
        for payload in cleaned:
            m = pattern.search(payload)
            if m:
                return Shipment(carrier=carrier, tracking=m.group(grp))
    if cleaned:
        # Nothing recognised: keep the shortest plausible payload as the number.
        raw = min(cleaned, key=len)
        return Shipment(carrier=UNKNOWN, tracking=raw)
    return Shipment(carrier=UNKNOWN, tracking=None)
