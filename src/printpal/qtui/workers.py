"""Background workers so the UI never blocks on a slow machine.

Detection and batch printing run on the global QThreadPool; results come back to
the GUI thread as Qt signals.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from printpal.batch import PrintQueue
from printpal.config import Config
from printpal.pipeline import process_file


class DetectSignals(QObject):
    done = Signal(list)       # list[ProcessedLabel]
    error = Signal(str)
    progress = Signal(str)


class DetectWorker(QRunnable):
    """Run process_file(path) off the GUI thread."""

    def __init__(self, path: str, config: Config):
        super().__init__()
        self.path = path
        self.config = config
        self.signals = DetectSignals()

    @Slot()
    def run(self) -> None:
        try:
            labels = process_file(
                self.path, self.config,
                progress=lambda msg, i, n: self.signals.progress.emit(msg))
            self.signals.done.emit(labels)
        except Exception as e:  # noqa: BLE001 - surface to the user
            self.signals.error.emit(str(e))


class EnqueueSignals(QObject):
    item_changed = Signal(object)     # QueueItem (as each is detected)
    finished = Signal()


class EnqueueWorker(QRunnable):
    """Detect and enqueue a batch of files off the GUI thread."""

    def __init__(self, queue: PrintQueue, paths: list[str]):
        super().__init__()
        self.queue = queue
        self.paths = paths
        self.signals = EnqueueSignals()

    @Slot()
    def run(self) -> None:
        prev = self.queue.on_change
        self.queue.on_change = lambda item: self.signals.item_changed.emit(item)
        try:
            self.queue.add_many(self.paths)
        finally:
            self.queue.on_change = prev
            self.signals.finished.emit()


class BatchSignals(QObject):
    item_changed = Signal(object)     # QueueItem
    finished = Signal()


class BatchWorker(QRunnable):
    """Print an entire queue off the GUI thread, streaming per-item updates."""

    def __init__(self, queue: PrintQueue, routing_enabled: bool):
        super().__init__()
        self.queue = queue
        self.routing_enabled = routing_enabled
        self.signals = BatchSignals()

    @Slot()
    def run(self) -> None:
        prev = self.queue.on_change
        self.queue.on_change = lambda item: self.signals.item_changed.emit(item)
        try:
            self.queue.print_all(routing_enabled=self.routing_enabled)
        finally:
            self.queue.on_change = prev
            self.signals.finished.emit()
