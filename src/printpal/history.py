"""Persistent print history with one-click reprint.

Every print is logged to a small SQLite database in AppData: a thumbnail, the
date, the carrier and tracking number (decoded from the label's barcode), and
the exact print-ready image so a reprint is faithful and independent of the
original file (which may be long gone from Downloads).

Pure-stdlib + Pillow, no UI or Windows imports, so it is unit-tested headless.
The database path and image directory are injectable for tests.
"""
from __future__ import annotations

import io
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from printpal.config import _CONFIG_DIR

THUMB_MAX = 240  # longest thumbnail side, px


@dataclass
class HistoryEntry:
    created_at: float
    source_name: str
    carrier: str
    tracking: str | None
    kind: str
    copies: int
    printer: str
    id: int | None = None
    page_index: int = 0
    region_index: int = 0
    width_px: int = 0
    height_px: int = 0
    dpi: int = 300
    image_name: str | None = None       # reprint PNG filename under the image dir
    thumb_png: bytes | None = None       # small PNG for the list, stored in the DB


def _make_thumb(image: Image.Image) -> bytes:
    thumb = image.convert("RGB").copy()
    thumb.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
    buf = io.BytesIO()
    thumb.save(buf, format="PNG")
    return buf.getvalue()


class History:
    def __init__(self, base_dir: str | Path | None = None):
        self.base = Path(base_dir) if base_dir else _CONFIG_DIR
        self.db_path = self.base / "history.db"
        self.img_dir = self.base / "history"
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        self.img_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS prints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    source_name TEXT,
                    carrier TEXT,
                    tracking TEXT,
                    kind TEXT,
                    copies INTEGER,
                    printer TEXT,
                    page_index INTEGER,
                    region_index INTEGER,
                    width_px INTEGER,
                    height_px INTEGER,
                    dpi INTEGER,
                    image_name TEXT,
                    thumb_png BLOB
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_prints_created ON prints(created_at DESC)")

    # -- writing ---------------------------------------------------------------
    def record(self, entry: HistoryEntry, print_image: Image.Image) -> int:
        """Store a print: save its image + thumbnail, insert the row, return id."""
        image_name = f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}.png"
        print_image.convert("RGB").save(self.img_dir / image_name, format="PNG")
        thumb = _make_thumb(print_image)
        w, h = print_image.size
        with self._connect() as c:
            cur = c.execute(
                """INSERT INTO prints (created_at, source_name, carrier, tracking, kind,
                       copies, printer, page_index, region_index, width_px, height_px,
                       dpi, image_name, thumb_png)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (entry.created_at or time.time(), entry.source_name, entry.carrier,
                 entry.tracking, entry.kind, entry.copies, entry.printer,
                 entry.page_index, entry.region_index, w, h, entry.dpi,
                 image_name, thumb))
            return int(cur.lastrowid)

    # -- reading ---------------------------------------------------------------
    def _row_to_entry(self, row: sqlite3.Row) -> HistoryEntry:
        return HistoryEntry(
            id=row["id"], created_at=row["created_at"], source_name=row["source_name"],
            carrier=row["carrier"], tracking=row["tracking"], kind=row["kind"],
            copies=row["copies"], printer=row["printer"], page_index=row["page_index"],
            region_index=row["region_index"], width_px=row["width_px"],
            height_px=row["height_px"], dpi=row["dpi"], image_name=row["image_name"],
            thumb_png=row["thumb_png"])

    def recent(self, limit: int = 100) -> list[HistoryEntry]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM prints ORDER BY created_at DESC, id DESC LIMIT ?",
                             (limit,)).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def get(self, entry_id: int) -> HistoryEntry | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM prints WHERE id = ?", (entry_id,)).fetchone()
        return self._row_to_entry(row) if row else None

    def last(self) -> HistoryEntry | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM prints ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        return self._row_to_entry(row) if row else None

    def load_print_image(self, entry: HistoryEntry) -> Image.Image | None:
        """The exact image that was printed, for reprint. None if it's gone."""
        if not entry.image_name:
            return None
        path = self.img_dir / entry.image_name
        if not path.is_file():
            return None
        return Image.open(path).convert("RGB")

    # -- maintenance -----------------------------------------------------------
    def delete(self, entry_id: int) -> None:
        entry = self.get(entry_id)
        if entry and entry.image_name:
            try:
                (self.img_dir / entry.image_name).unlink()
            except OSError:
                pass
        with self._connect() as c:
            c.execute("DELETE FROM prints WHERE id = ?", (entry_id,))

    def count(self) -> int:
        with self._connect() as c:
            return int(c.execute("SELECT COUNT(*) FROM prints").fetchone()[0])

    def clear(self) -> None:
        for entry in self.recent(limit=10_000):
            if entry.id is not None:
                self.delete(entry.id)
