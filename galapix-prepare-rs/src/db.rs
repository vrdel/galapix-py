use anyhow::{Context, Result};
use rusqlite::{params, Connection, OptionalExtension};
use std::fs;
use std::path::{Path, PathBuf};

use crate::vips;

const TILE_SIZE: u32 = 256;
const TILE_STORE_BATCH_SIZE: usize = 256;

const SCHEMA: &str = r#"
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
"#;

#[derive(Debug, Clone)]
pub struct FileMeta {
    pub url: String,
    pub mtime_ns: i64,
    pub size_bytes: i64,
    pub width: i64,
    pub height: i64,
    pub image_format: String,
}

#[derive(Debug, Clone)]
pub struct TileRecord {
    pub scale: i64,
    pub x: i64,
    pub y: i64,
    pub width: i64,
    pub height: i64,
    pub jpeg_bytes: Vec<u8>,
}

impl FileMeta {
    pub fn probe(path: &Path) -> Result<Self> {
        let resolved = path
            .canonicalize()
            .with_context(|| format!("failed to resolve {}", path.display()))?;
        let metadata = fs::metadata(&resolved)
            .with_context(|| format!("failed to stat {}", resolved.display()))?;
        let mtime_ns = metadata
            .modified()
            .context("failed to read modified time")?
            .duration_since(std::time::UNIX_EPOCH)
            .context("file modified time predates UNIX_EPOCH")?
            .as_nanos() as i64;
        let size_bytes = metadata.len() as i64;
        let image = vips::Image::open_sequential(&resolved)?;
        let width = image.width() as i64;
        let height = image.height() as i64;
        let image_format = resolved
            .extension()
            .and_then(|ext| ext.to_str())
            .unwrap_or("unknown")
            .to_ascii_lowercase();
        Ok(Self {
            url: resolved.to_string_lossy().into_owned(),
            mtime_ns,
            size_bytes,
            width,
            height,
            image_format,
        })
    }

    pub fn thumbnail_scale(&self) -> i64 {
        let mut longest = self.width.max(self.height).max(1);
        let mut scale = 0;
        while longest > TILE_SIZE as i64 {
            longest /= 2;
            scale += 1;
        }
        scale
    }
}

pub struct Database {
    path: PathBuf,
    conn: Connection,
}

impl Database {
    pub fn open(root: &Path) -> Result<Self> {
        fs::create_dir_all(root).with_context(|| format!("failed to create {}", root.display()))?;
        let path = root.join("cache.sqlite3");
        let conn = Connection::open(&path)
            .with_context(|| format!("failed to open {}", path.display()))?;
        conn.execute_batch(SCHEMA)?;
        conn.execute("PRAGMA foreign_keys=ON", [])?;
        Ok(Self { path, conn })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn is_unchanged_and_complete(&mut self, meta: &FileMeta) -> Result<bool> {
        let row = self
            .conn
            .query_row(
                r#"
                SELECT
                  f.mtime_ns,
                  f.size_bytes,
                  f.width,
                  f.height,
                  f.image_format,
                  MIN(t.scale),
                  MAX(t.scale)
                FROM files AS f
                LEFT JOIN tiles AS t ON t.file_id = f.file_id
                WHERE f.url = ?1
                GROUP BY f.file_id
                "#,
                params![meta.url],
                |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, i64>(1)?,
                        row.get::<_, i64>(2)?,
                        row.get::<_, i64>(3)?,
                        row.get::<_, String>(4)?,
                        row.get::<_, Option<i64>>(5)?,
                        row.get::<_, Option<i64>>(6)?,
                    ))
                },
            )
            .optional()?;

        let Some((mtime_ns, size_bytes, width, height, image_format, min_scale, max_scale)) = row
        else {
            return Ok(false);
        };

        let is_current = mtime_ns == meta.mtime_ns
            && size_bytes == meta.size_bytes
            && width == meta.width
            && height == meta.height
            && image_format == meta.image_format;
        let is_complete = min_scale == Some(0) && max_scale == Some(meta.thumbnail_scale());
        Ok(is_current && is_complete)
    }

    pub fn bulk_writes<T, F>(&mut self, f: F) -> Result<T>
    where
        F: FnOnce(&mut Self) -> Result<T>,
    {
        self.conn.execute("BEGIN", [])?;
        match f(self) {
            Ok(value) => {
                self.conn.execute("COMMIT", [])?;
                Ok(value)
            }
            Err(err) => {
                let _ = self.conn.execute("ROLLBACK", []);
                Err(err)
            }
        }
    }

    pub fn store_file_entry(&mut self, entry: &FileMeta) -> Result<i64> {
        self.conn.execute(
            r#"
            INSERT INTO files(url, mtime_ns, size_bytes, width, height, image_format)
            VALUES(?1, ?2, ?3, ?4, ?5, ?6)
            ON CONFLICT(url) DO UPDATE SET
              mtime_ns = excluded.mtime_ns,
              size_bytes = excluded.size_bytes,
              width = excluded.width,
              height = excluded.height,
              image_format = excluded.image_format
            "#,
            params![
                entry.url,
                entry.mtime_ns,
                entry.size_bytes,
                entry.width,
                entry.height,
                entry.image_format
            ],
        )?;

        let file_id = self.conn.query_row(
            "SELECT file_id FROM files WHERE url = ?1",
            params![entry.url],
            |row| row.get(0),
        )?;
        Ok(file_id)
    }

    pub fn delete_file_by_url(&mut self, url: &str) -> Result<bool> {
        let deleted = self
            .conn
            .execute("DELETE FROM files WHERE url = ?1", params![url])?;
        Ok(deleted > 0)
    }

    pub fn store_tiles(&mut self, file_id: i64, tiles: &[TileRecord]) -> Result<()> {
        for chunk in tiles.chunks(TILE_STORE_BATCH_SIZE) {
            let mut statement = self.conn.prepare(
                r#"
                INSERT INTO tiles(file_id, scale, x, y, width, height, jpeg_bytes)
                VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7)
                ON CONFLICT(file_id, scale, x, y) DO UPDATE SET
                  width = excluded.width,
                  height = excluded.height,
                  jpeg_bytes = excluded.jpeg_bytes
                "#,
            )?;
            for tile in chunk {
                statement.execute(params![
                    file_id,
                    tile.scale,
                    tile.x,
                    tile.y,
                    tile.width,
                    tile.height,
                    tile.jpeg_bytes
                ])?;
            }
        }
        Ok(())
    }
}
