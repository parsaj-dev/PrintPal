"""Background workers so the UI never blocks on a slow machine.

Detection, printing (render + spool + history) and batch work all run on the
global QThreadPool; results come back to the GUI thread as Qt signals. Nothing
here touches a widget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from printpal.batch import PrintQueue
from printpal.config import Config


class DetectSignals(QObject):
    done = Signal(int, list)      # (generation, list[ProcessedLabel])
    error = Signal(int, str)      # (generation, message)
    progress = Signal(int, str)   # (generation, message)


class DetectWorker(QRunnable):
    """Run process_file(path) off the GUI thread.

    ``generation`` tags every signal so the window can ignore a result the user
    has since cancelled or replaced. ``prerender`` runs after ``done`` is
    emitted -- it renders the print image in the background while the user looks
    at the preview, so Print (or auto-print) doesn't have to wait for it.
    """

    def __init__(self, path: str, config: Config, generation: int,
                 prerender: Callable[[list], None] | None = None):
        super().__init__()
        self.path = path
        self.config = config
        self.generation = generation
        self.prerender = prerender
        self.signals = DetectSignals()

    @Slot()
    def run(self) -> None:
        from printpal.pipeline import process_file  # heavy; loaded on first use
        gen = self.generation
        try:
            labels = process_file(
                self.path, self.config,
                progress=lambda msg, i, n: self.signals.progress.emit(gen, msg))
        except Exception as e:  # noqa: BLE001 - surface to the user
            self.signals.error.emit(gen, str(e) or e.__class__.__name__)
            return
        self.signals.done.emit(gen, labels)
        if self.prerender is not None:
            try:
                self.prerender(labels)
            except Exception:  # noqa: BLE001 - the print path will retry and report
                pass


@dataclass
class PrintTask:
    """One thing to print: how to get its image, where it goes, and what to do
    once it has printed (e.g. log it to history)."""
    render: Callable[[], Image.Image]
    printer: str
    copies: int
    after: Callable[[Image.Image], None] | None = None


class PrintSignals(QObject):
    finished = Signal(int, list)     # (printed count, list of error strings)


class PrintWorker(QRunnable):
    """Render, spool and record a list of PrintTasks off the GUI thread."""

    def __init__(self, tasks: list[PrintTask], print_fn):
        super().__init__()
        self.tasks = tasks
        self.print_fn = print_fn
        self.signals = PrintSignals()

    @Slot()
    def run(self) -> None:
        ok, errors = 0, []
        for t in self.tasks:
            try:
                image = t.render()
            except Exception as e:  # noqa: BLE001
                errors.append(f"Couldn't render the label: {e}")
                continue
            try:
                self.print_fn(image, t.printer, t.copies)
            except Exception as e:  # noqa: BLE001
                errors.append(str(e) or e.__class__.__name__)
                continue
            ok += 1
            if t.after is not None:
                try:
                    t.after(image)
                except Exception:  # noqa: BLE001 - history is best-effort
                    pass
        self.signals.finished.emit(ok, errors)


class QueueSignals(QObject):
    item_changed = Signal(object)     # QueueItem
    finished = Signal()


class EnqueueWorker(QRunnable):
    """Detect and enqueue a batch of files off the GUI thread."""

    def __init__(self, queue: PrintQueue, paths: list[str]):
        super().__init__()
        self.queue = queue
        self.paths = paths
        self.signals = QueueSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.queue.add_many(self.paths, on_change=self.signals.item_changed.emit)
        finally:
            self.signals.finished.emit()


class BatchWorker(QRunnable):
    """Print a whole queue (or retry one item) off the GUI thread, streaming
    per-item updates."""

    def __init__(self, queue: PrintQueue, routing_enabled: bool, item=None):
        super().__init__()
        self.queue = queue
        self.routing_enabled = routing_enabled
        self.item = item
        self.signals = QueueSignals()

    @Slot()
    def run(self) -> None:
        emit = self.signals.item_changed.emit
        try:
            if self.item is not None:
                self.queue.retry(self.item, routing_enabled=self.routing_enabled, on_change=emit)
            else:
                self.queue.print_all(routing_enabled=self.routing_enabled, on_change=emit)
        finally:
            self.signals.finished.emit()
