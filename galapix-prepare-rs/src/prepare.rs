use crate::db::{Database, FileMeta, TileRecord};
use crate::vips;
use anyhow::{Context, Result};
use std::collections::{HashMap, VecDeque};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;

const JPEG_QUALITY: i32 = 85;
const TILE_SIZE: i32 = 256;
const TILE_BATCH_SIZE: usize = 128;

pub fn run(db: &mut Database, metas: Vec<FileMeta>, threads: usize) -> Result<PrepareStats> {
    if metas.is_empty() {
        return Ok(PrepareStats::default());
    }

    let worker_count = threads.max(1);
    let queue = Arc::new(Mutex::new(VecDeque::from(metas)));
    let (tx, rx) = mpsc::sync_channel::<PrepareMessage>(worker_count * 2);

    let mut handles = Vec::with_capacity(worker_count);
    for _ in 0..worker_count {
        let queue = Arc::clone(&queue);
        let tx = tx.clone();
        handles.push(thread::spawn(move || worker_loop(queue, tx)));
    }
    drop(tx);

    let writer_result = db.bulk_writes(|db| writer_loop(db, rx));

    for handle in handles {
        match handle.join() {
            Ok(Ok(())) => {}
            Ok(Err(err)) => return Err(err),
            Err(_) => anyhow::bail!("worker thread panicked"),
        }
    }

    writer_result
}

#[derive(Debug, Default)]
pub struct PrepareStats {
    pub prepared_images: usize,
    pub stored_tiles: usize,
}

enum PrepareMessage {
    Begin { meta: FileMeta },
    TileBatch { url: String, tiles: Vec<TileRecord> },
    Done { url: String },
    Error { url: String, message: String },
}

fn worker_loop(
    queue: Arc<Mutex<VecDeque<FileMeta>>>,
    tx: mpsc::SyncSender<PrepareMessage>,
) -> Result<()> {
    while let Some(meta) = pop_work(&queue)? {
        if let Err(err) = prepare_one(&meta, &tx) {
            tx.send(PrepareMessage::Error {
                url: meta.url.clone(),
                message: format!("{err:#}"),
            })
            .ok();
            break;
        }
    }
    vips::shutdown_thread();
    Ok(())
}

fn pop_work(queue: &Arc<Mutex<VecDeque<FileMeta>>>) -> Result<Option<FileMeta>> {
    let mut queue = queue
        .lock()
        .map_err(|_| anyhow::anyhow!("work queue poisoned"))?;
    Ok(queue.pop_front())
}

fn prepare_one(meta: &FileMeta, tx: &mpsc::SyncSender<PrepareMessage>) -> Result<()> {
    tx.send(PrepareMessage::Begin { meta: meta.clone() })
        .context("failed to announce prepared image")?;

    let image = vips::Image::open_random(meta.url.as_ref())?;
    let max_scale = meta.thumbnail_scale();

    for scale in 0..=max_scale {
        let factor = 1_i64 << scale;
        let width = ((meta.width + factor - 1) / factor).max(1) as i32;
        let height = ((meta.height + factor - 1) / factor).max(1) as i32;
        let scaled = image.thumbnail_force(width, height)?;

        let x_tiles = (scaled.width() + TILE_SIZE - 1) / TILE_SIZE;
        let y_tiles = (scaled.height() + TILE_SIZE - 1) / TILE_SIZE;
        let mut batch = Vec::with_capacity(TILE_BATCH_SIZE);

        for y in 0..y_tiles {
            for x in 0..x_tiles {
                let left = x * TILE_SIZE;
                let top = y * TILE_SIZE;
                let tile_width = (scaled.width() - left).min(TILE_SIZE);
                let tile_height = (scaled.height() - top).min(TILE_SIZE);
                let tile = scaled.crop(left, top, tile_width, tile_height)?;
                batch.push(TileRecord {
                    scale,
                    x: x as i64,
                    y: y as i64,
                    width: tile_width as i64,
                    height: tile_height as i64,
                    jpeg_bytes: tile.save_jpeg(JPEG_QUALITY)?,
                });

                if batch.len() >= TILE_BATCH_SIZE {
                    tx.send(PrepareMessage::TileBatch {
                        url: meta.url.clone(),
                        tiles: std::mem::take(&mut batch),
                    })
                    .context("failed to send tile batch")?;
                }
            }
        }

        if !batch.is_empty() {
            tx.send(PrepareMessage::TileBatch {
                url: meta.url.clone(),
                tiles: batch,
            })
            .context("failed to send final tile batch")?;
        }
    }

    tx.send(PrepareMessage::Done {
        url: meta.url.clone(),
    })
    .context("failed to send completion notification")?;
    Ok(())
}

fn writer_loop(db: &mut Database, rx: mpsc::Receiver<PrepareMessage>) -> Result<PrepareStats> {
    let mut file_ids = HashMap::<String, i64>::new();
    let mut stats = PrepareStats::default();

    while let Ok(message) = rx.recv() {
        match message {
            PrepareMessage::Begin { meta } => {
                db.delete_file_by_url(&meta.url)?;
                let file_id = db.store_file_entry(&meta)?;
                file_ids.insert(meta.url, file_id);
            }
            PrepareMessage::TileBatch { url, tiles } => {
                let file_id = file_ids
                    .get(&url)
                    .copied()
                    .with_context(|| format!("missing file id for {url}"))?;
                stats.stored_tiles += tiles.len();
                db.store_tiles(file_id, &tiles)?;
            }
            PrepareMessage::Done { url } => {
                file_ids.remove(&url);
                stats.prepared_images += 1;
            }
            PrepareMessage::Error { url, message } => {
                anyhow::bail!("failed to prepare {url}: {message}");
            }
        }
    }

    if !file_ids.is_empty() {
        anyhow::bail!("worker channel closed before all images finished");
    }

    Ok(stats)
}
