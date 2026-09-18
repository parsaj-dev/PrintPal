"""Batch print queue.

Drop a folder, multi-select files, or one PDF holding many labels, and print
them all with one click, tracking each label's status: queued -> printing ->
done, or failed with a retry. Detection expands each source into one queue item
per label (N-up pages included), so the status list is per-label.

Designed to pair with a future Downloads-watcher: it just calls ``add()`` /
``add_many()`` as files arrive. Printing is injected as ``print_fn`` so the whole
queue is unit-tested headless; the desktop app passes ``printing.print_label``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PIL import Image

from printpal import routing
from printpal.carrier import parse_tracking
from printpal.config import Config
from printpal.history import History, HistoryEntry
from printpal.pipeline import ProcessedLabel, process_file

# Item statuses.
QUEUED = "queued"
PRINTING = "printing"
DONE = "done"
FAILED = "failed"

# print_fn(image, printer_name, copies) -> None
PrintFn = Callable[[Image.Image, str, int], None]
ChangeFn = Callable[["QueueItem"], None]


@dataclass
class QueueItem:
    id: int
    source: str
    title: str
    label: ProcessedLabel | None = None
    carrier: str = "Unknown"
    tracking: str | None = None
    kind: str = "label"
    status: str = QUEUED
    error: str | None = None
    retries: int = 0
    printer: str | None = None          # where it was (or will be) printed

    @property
    def is_done(self) -> bool:
        return self.status == DONE


class PrintQueue:
    """A flat, per-label print queue with retry and optional history logging."""

    def __init__(self, config: Config, print_fn: PrintFn,
                 history: History | None = None, on_change: ChangeFn | None = None):
        self.config = config
        self.print_fn = print_fn
        self.history = history
        self.on_change = on_change
        self.items: list[QueueItem] = []
        self._next_id = 1

    # -- building the queue ----------------------------------------------------
    def _new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def add(self, source: str, title: str | None = None) -> list[QueueItem]:
        """Detect `source` and enqueue one item per label it contains.

        Detection failures (or a file with nothing printable) enqueue a single
        FAILED item so the user sees why nothing came out.
        """
        title = title or source
        try:
            labels = process_file(source, self.config)
        except Exception as e:  # noqa: BLE001 - surface any read/detect failure
            item = QueueItem(self._new_id(), source, title, status=FAILED,
                             error=f"Could not read: {e}")
            self.items.append(item)
            self._changed(item)
            return [item]

        printable = [lab for lab in labels if lab.is_printable]
        if not printable:
            item = QueueItem(self._new_id(), source, title, status=FAILED,
                             error="No label or document found to print.")
            self.items.append(item)
            self._changed(item)
            return [item]

        created = []
        for lab in printable:
            ship = parse_tracking(lab.result.barcode_data)
            item = QueueItem(self._new_id(), source, title, label=lab,
                             carrier=ship.carrier, tracking=ship.tracking,
                             kind=lab.kind)
            self.items.append(item)
            created.append(item)
            self._changed(item)
        return created

    def add_many(self, sources: list[str]) -> list[QueueItem]:
        out: list[QueueItem] = []
        for src in sources:
            out.extend(self.add(src))
        return out

    # -- printing --------------------------------------------------------------
    def pending(self) -> list[QueueItem]:
        return [it for it in self.items if it.status in (QUEUED, FAILED) and it.label is not None]

    def print_all(self, routing_enabled: bool = False) -> None:
        """Print every queued (and retry-eligible failed) item, in order."""
        for item in list(self.pending()):
            self._print_item(item, routing_enabled)

    def retry(self, item: QueueItem, routing_enabled: bool = False) -> None:
        if item.label is None:
            return
        self._print_item(item, routing_enabled)

    def _print_item(self, item: QueueItem, routing_enabled: bool) -> None:
        item.status = PRINTING
        item.error = None
        self._changed(item)
        try:
            image = item.label.render_print_image(self.config)
            decision = routing.printer_for(item.kind, self.config, routing_enabled)
            item.printer = decision.printer
            self.print_fn(image, decision.printer, self.config.copies)
            self._record_history(item, image)
            item.status = DONE
        except Exception as e:  # noqa: BLE001 - a bad label must not stop the batch
            item.status = FAILED
            item.error = str(e)
            item.retries += 1
        self._changed(item)

    def _record_history(self, item: QueueItem, image: Image.Image) -> None:
        if self.history is None:
            return
        try:
            lab = item.label
            entry = HistoryEntry(
                created_at=0.0, source_name=item.title, carrier=item.carrier,
                tracking=item.tracking, kind=item.kind, copies=self.config.copies,
                printer=item.printer or self.config.printer,
                page_index=lab.page_index if lab else 0,
                region_index=lab.region_index if lab else 0,
                dpi=self.config.print_dpi)
            self.history.record(entry, image)
        except Exception:  # noqa: BLE001 - history is best-effort, never fatal
            pass

    def _changed(self, item: QueueItem) -> None:
        if self.on_change:
            try:
                self.on_change(item)
            except Exception:  # noqa: BLE001
                pass

    # -- summary ---------------------------------------------------------------
    def summary(self) -> dict[str, int]:
        out = {QUEUED: 0, PRINTING: 0, DONE: 0, FAILED: 0}
        for it in self.items:
            out[it.status] = out.get(it.status, 0) + 1
        return out
