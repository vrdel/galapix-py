from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional
from urllib.parse import unquote, urlparse

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
    TILE_STORE_BATCH_SIZE = 256

    def __init__(self, root: Path) -> None:
        self.root, self.path = self._resolve_database_path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.legacy_schema = self._detect_legacy_schema()
        if not self.legacy_schema:
            self.conn.executescript(SCHEMA)
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def cleanup(self) -> None:
        self.conn.execute("DELETE FROM files")
        if not self.legacy_schema:
            self.conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('files')")
        self.conn.commit()
        self.conn.execute("VACUUM")
        self.conn.commit()

    def list_files(self) -> list[FileEntry]:
        if self.legacy_schema:
            rows = self.conn.execute(
                """
                SELECT fileid AS file_id, url, mtime, size AS size_bytes, width, height, format AS image_format
                FROM files ORDER BY url
                """
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT file_id, url, mtime_ns, size_bytes, width, height, image_format FROM files ORDER BY url"
            ).fetchall()
        return [self._row_to_file_entry(row) for row in rows]

    def get_file_entry(self, url: str) -> Optional[FileEntry]:
        if self.legacy_schema:
            row = self.conn.execute(
                """
                SELECT fileid AS file_id, url, mtime, size AS size_bytes, width, height, format AS image_format
                FROM files WHERE url IN (?, ?)
                """,
                self._url_lookup_values(url),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT file_id, url, mtime_ns, size_bytes, width, height, image_format FROM files WHERE url = ?",
                (url,),
            ).fetchone()
        return None if row is None else self._row_to_file_entry(row)

    @contextmanager
    def bulk_writes(self) -> Iterator[None]:
        self.conn.execute("BEGIN")
        try:
            yield
        except Exception:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()

    def store_file_entry(self, entry: FileEntry, commit: bool = True) -> FileEntry:
        if self.legacy_schema:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO files(url, mtime, size, width, height, format)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (entry.url, entry.mtime_ns // 1_000_000_000, entry.size_bytes, entry.width, entry.height, entry.image_format),
            )
        else:
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
        if commit:
            self.conn.commit()
        return self.get_file_entry(entry.url)  # type: ignore[return-value]

    def delete_file_entry(self, file_id: int, commit: bool = True) -> None:
        column = "fileid" if self.legacy_schema else "file_id"
        self.conn.execute(f"DELETE FROM files WHERE {column} = ?", (file_id,))
        if commit:
            self.conn.commit()

    def delete_file_by_url(self, url: str, commit: bool = True) -> bool:
        if self.legacy_schema:
            cursor = self.conn.execute("DELETE FROM files WHERE url IN (?, ?)", self._url_lookup_values(url))
        else:
            cursor = self.conn.execute("DELETE FROM files WHERE url = ?", (url,))
        if commit:
            self.conn.commit()
        return cursor.rowcount > 0

    def store_tiles(self, file_id: int, tiles: Iterable[TileRecord], commit: bool = True) -> None:
        batch: list[tuple[int, int, int, int, int, int, bytes]] = []
        wrote_any = False
        for tile in tiles:
            batch.append((file_id, tile.scale, tile.x, tile.y, tile.width, tile.height, tile.jpeg_bytes))
            if len(batch) >= self.TILE_STORE_BATCH_SIZE:
                self._store_tile_rows(batch)
                batch.clear()
                wrote_any = True
        if batch:
            self._store_tile_rows(batch)
            wrote_any = True
        if commit and wrote_any:
            self.conn.commit()

    def get_tile(self, file_id: int, scale: int, x: int, y: int) -> Optional[TileRecord]:
        if self.legacy_schema:
            row = self.conn.execute(
                """
                SELECT t.fileid AS file_id, t.scale, t.x, t.y, t.data AS jpeg_bytes,
                       f.width AS image_width, f.height AS image_height
                FROM tiles t
                JOIN files f ON f.fileid = t.fileid
                WHERE t.fileid = ? AND t.scale = ? AND t.x = ? AND t.y = ?
                """,
                (file_id, scale, x, y),
            ).fetchone()
        else:
            row = self.conn.execute(
                """
                SELECT file_id, scale, x, y, width, height, jpeg_bytes
                FROM tiles WHERE file_id = ? AND scale = ? AND x = ? AND y = ?
                """,
                (file_id, scale, x, y),
            ).fetchone()
        return None if row is None else self._row_to_tile_record(row)

    def get_min_max_scale(self, file_id: int) -> tuple[Optional[int], Optional[int]]:
        file_column = "fileid" if self.legacy_schema else "file_id"
        row = self.conn.execute(
            f"SELECT MIN(scale) AS min_scale, MAX(scale) AS max_scale FROM tiles WHERE {file_column} = ?",
            (file_id,),
        ).fetchone()
        if row is None or row["min_scale"] is None:
            return None, None
        return int(row["min_scale"]), int(row["max_scale"])

    def count_tiles_for_file(self, file_id: int) -> int:
        file_column = "fileid" if self.legacy_schema else "file_id"
        row = self.conn.execute(
            f"SELECT COUNT(*) AS tile_count FROM tiles WHERE {file_column} = ?",
            (file_id,),
        ).fetchone()
        return 0 if row is None else int(row["tile_count"])

    def file_exists_and_matches(self, entry: FileEntry) -> bool:
        from pathlib import Path

        path = Path(self._path_from_url(entry.url))
        if not path.exists():
            return False
        stat = path.stat()
        if self.legacy_schema:
            return stat.st_size == entry.size_bytes and int(stat.st_mtime) == entry.mtime_ns // 1_000_000_000
        return stat.st_size == entry.size_bytes and stat.st_mtime_ns == entry.mtime_ns

    def _row_to_file_entry(self, row: sqlite3.Row) -> FileEntry:
        raw_url = str(row["url"])
        mtime_ns = int(row["mtime"]) * 1_000_000_000 if "mtime" in row.keys() else int(row["mtime_ns"])
        return FileEntry(
            file_id=int(row["file_id"]),
            url=self._path_from_url(raw_url),
            mtime_ns=mtime_ns,
            size_bytes=int(row["size_bytes"]),
            width=int(row["width"]),
            height=int(row["height"]),
            image_format=str(row["image_format"]),
        )

    def _store_tile_rows(self, rows: list[tuple[int, int, int, int, int, int, bytes]]) -> None:
        if self.legacy_schema:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO tiles(fileid, scale, x, y, data, quality, format)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                [(file_id, scale, x, y, jpeg_bytes, 85, "jpg") for file_id, scale, x, y, _, _, jpeg_bytes in rows],
            )
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

    def _row_to_tile_record(self, row: sqlite3.Row) -> TileRecord:
        if "width" in row.keys() and "height" in row.keys():
            width = int(row["width"])
            height = int(row["height"])
        else:
            width, height = self._legacy_tile_dimensions(
                int(row["image_width"]),
                int(row["image_height"]),
                int(row["scale"]),
                int(row["x"]),
                int(row["y"]),
            )
        return TileRecord(
            file_id=int(row["file_id"]),
            scale=int(row["scale"]),
            x=int(row["x"]),
            y=int(row["y"]),
            width=width,
            height=height,
            jpeg_bytes=bytes(row["jpeg_bytes"]),
        )

    @staticmethod
    def _resolve_database_path(root: Path) -> tuple[Path, Path]:
        if root.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            return root.parent, root
        return root, root / "cache.sqlite3"

    def _detect_legacy_schema(self) -> bool:
        files_columns = self._table_columns("files")
        tiles_columns = self._table_columns("tiles")
        if not files_columns and not tiles_columns:
            return False
        return {"fileid", "mtime", "size", "format"}.issubset(files_columns) and {"fileid", "data"}.issubset(tiles_columns)

    def _table_columns(self, table: str) -> set[str]:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row["name"]) for row in rows}

    @staticmethod
    def _path_from_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme == "file":
            return unquote(parsed.path)
        return url

    @staticmethod
    def _file_url_from_path(path: str) -> str:
        return Path(path).resolve(strict=False).as_uri()

    def _url_lookup_values(self, url: str) -> tuple[str, str]:
        path = self._path_from_url(url)
        return path, self._file_url_from_path(path)

    @staticmethod
    def _legacy_tile_dimensions(image_width: int, image_height: int, scale: int, x: int, y: int) -> tuple[int, int]:
        factor = 2 ** scale
        scaled_width = max(1, math.ceil(image_width / factor))
        scaled_height = max(1, math.ceil(image_height / factor))
        return min(256, scaled_width - x * 256), min(256, scaled_height - y * 256)
