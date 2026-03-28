mod db;
mod discover;
mod prepare;
mod vips;

use anyhow::{bail, Context, Result};
use clap::Parser;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use crate::db::{Database, FileMeta};

#[derive(Parser, Debug)]
#[command(name = "galapix-prepare-rs")]
#[command(about = "Native/offline cache builder for galapix-py")]
struct Cli {
    #[arg(short = 'd', long = "database", default_value = "~/.galapix-py")]
    database: String,

    #[arg(short = 't', long = "threads", default_value_t = 4)]
    threads: usize,

    #[arg(required = true)]
    paths: Vec<PathBuf>,
}

fn main() -> Result<()> {
    let started_at = Instant::now();
    let cli = Cli::parse();
    let threads = cli.threads.max(1);
    let db_root = expand_home(&cli.database)?;
    vips::initialize("galapix-prepare-rs", threads)?;
    let paths = discover::expand_paths(&cli.paths)?;
    if paths.is_empty() {
        bail!("no supported image files found");
    }

    let mut db = Database::open(&db_root)?;
    let mut skipped = 0usize;
    let mut pending = Vec::new();

    for path in &paths {
        let meta = FileMeta::probe(path)?;
        if db.is_unchanged_and_complete(&meta)? {
            skipped += 1;
        } else {
            pending.push(meta);
        }
    }

    println!("galapix-prepare-rs");
    println!("  database: {}", db.path().display());
    println!("  discovered: {}", paths.len());
    println!("  skipped: {}", skipped);
    println!("  pending: {}", pending.len());
    println!("  threads: {}", threads);

    if pending.is_empty() {
        println!("  elapsed: {}", format_elapsed(started_at.elapsed()));
        return Ok(());
    }

    let stats = prepare::run(&mut db, pending, threads)?;
    println!("  prepared: {}", stats.prepared_images);
    println!("  stored_tiles: {}", stats.stored_tiles);
    println!("  elapsed: {}", format_elapsed(started_at.elapsed()));
    Ok(())
}

fn expand_home(value: &str) -> Result<PathBuf> {
    if let Some(stripped) = value.strip_prefix("~/") {
        let home = std::env::var("HOME").context("HOME is not set")?;
        Ok(PathBuf::from(home).join(stripped))
    } else {
        Ok(PathBuf::from(value))
    }
}

fn format_elapsed(duration: Duration) -> String {
    let seconds = duration.as_secs_f64();
    if seconds >= 60.0 {
        format!("{seconds:.2}s ({:.2}m)", seconds / 60.0)
    } else {
        format!("{seconds:.2}s")
    }
}
