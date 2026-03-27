from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .models import FileEntry, TileRecord


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS files (
  file_id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL UNIQUE,
  mtime_ns INTEGER NOT NULL,
  size_bytes INTEGER NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  image_format TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tiles (
  file_id INTEGER NOT NULL,
  scale INTEGER NOT NULL,
  x INTEGER NOT NULL,
  y INTEGER NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  jpeg_bytes BLOB NOT NULL,
  PRIMARY KEY (file_id, scale, x, y),
  FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tiles_file_scale ON tiles(file_id, scale);
"""


class Database:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "cache.sqlite3"
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def cleanup(self) -> None:
        self.conn.execute("VACUUM")
        self.conn.commit()

    def list_files(self) -> list[FileEntry]:
        rows = self.conn.execute(
            "SELECT file_id, url, mtime_ns, size_bytes, width, height, image_format FROM files ORDER BY url"
        ).fetchall()
        return [self._row_to_file_entry(row) for row in rows]

    def get_file_entry(self, url: str) -> Optional[FileEntry]:
        row = self.conn.execute(
            "SELECT file_id, url, mtime_ns, size_bytes, width, height, image_format FROM files WHERE url = ?",
            (url,),
        ).fetchone()
        return None if row is None else self._row_to_file_entry(row)

    def store_file_entry(self, entry: FileEntry) -> FileEntry:
        self.conn.execute(
            """
            INSERT INTO files(url, mtime_ns, size_bytes, width, height, image_format)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              mtime_ns = excluded.mtime_ns,
              size_bytes = excluded.size_bytes,
              width = excluded.width,
              height = excluded.height,
              image_format = excluded.image_format
            """,
            (entry.url, entry.mtime_ns, entry.size_bytes, entry.width, entry.height, entry.image_format),
        )
        self.conn.commit()
        return self.get_file_entry(entry.url)  # type: ignore[return-value]

    def delete_file_entry(self, file_id: int) -> None:
        self.conn.execute("DELETE FROM files WHERE file_id = ?", (file_id,))
        self.conn.commit()

    def store_tiles(self, file_id: int, tiles: Iterable[TileRecord]) -> None:
        rows = [
            (file_id, tile.scale, tile.x, tile.y, tile.width, tile.height, tile.jpeg_bytes)
            for tile in tiles
        ]
        if not rows:
            return
        self.conn.executemany(
            """
            INSERT INTO tiles(file_id, scale, x, y, width, height, jpeg_bytes)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id, scale, x, y) DO UPDATE SET
              width = excluded.width,
              height = excluded.height,
              jpeg_bytes = excluded.jpeg_bytes
            """,
            rows,
        )
        self.conn.commit()

    def get_tile(self, file_id: int, scale: int, x: int, y: int) -> Optional[TileRecord]:
        row = self.conn.execute(
            """
            SELECT file_id, scale, x, y, width, height, jpeg_bytes
            FROM tiles WHERE file_id = ? AND scale = ? AND x = ? AND y = ?
            """,
            (file_id, scale, x, y),
        ).fetchone()
        return None if row is None else self._row_to_tile_record(row)

    def get_min_max_scale(self, file_id: int) -> tuple[Optional[int], Optional[int]]:
        row = self.conn.execute(
            "SELECT MIN(scale) AS min_scale, MAX(scale) AS max_scale FROM tiles WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        if row is None or row["min_scale"] is None:
            return None, None
        return int(row["min_scale"]), int(row["max_scale"])

    def file_exists_and_matches(self, entry: FileEntry) -> bool:
        from pathlib import Path

        path = Path(entry.url)
        if not path.exists():
            return False
        stat = path.stat()
        return stat.st_size == entry.size_bytes and stat.st_mtime_ns == entry.mtime_ns

    def _row_to_file_entry(self, row: sqlite3.Row) -> FileEntry:
        return FileEntry(
            file_id=int(row["file_id"]),
            url=str(row["url"]),
            mtime_ns=int(row["mtime_ns"]),
            size_bytes=int(row["size_bytes"]),
            width=int(row["width"]),
            height=int(row["height"]),
            image_format=str(row["image_format"]),
        )

    def _row_to_tile_record(self, row: sqlite3.Row) -> TileRecord:
        return TileRecord(
            file_id=int(row["file_id"]),
            scale=int(row["scale"]),
            x=int(row["x"]),
            y=int(row["y"]),
            width=int(row["width"]),
            height=int(row["height"]),
            jpeg_bytes=bytes(row["jpeg_bytes"]),
        )
