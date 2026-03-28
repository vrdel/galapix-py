use anyhow::{Context, Result};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

const SUPPORTED_EXTENSIONS: &[&str] = &["jpg", "jpeg", "png", "tif", "tiff", "webp"];

pub fn expand_paths(paths: &[PathBuf]) -> Result<Vec<PathBuf>> {
    let mut seen = BTreeSet::new();
    let mut results = Vec::new();

    for path in paths {
        let expanded = expand_home(path);
        if expanded.is_dir() {
            for entry in WalkDir::new(&expanded).sort_by_file_name() {
                let entry = entry?;
                let candidate = entry.path();
                if candidate.is_file() && is_supported_image(candidate) {
                    let resolved = candidate
                        .canonicalize()
                        .with_context(|| format!("failed to resolve {}", candidate.display()))?;
                    if seen.insert(resolved.clone()) {
                        results.push(resolved);
                    }
                }
            }
        } else if expanded.exists() && is_supported_image(&expanded) {
            let resolved = expanded
                .canonicalize()
                .with_context(|| format!("failed to resolve {}", expanded.display()))?;
            if seen.insert(resolved.clone()) {
                results.push(resolved);
            }
        }
    }

    Ok(results)
}

fn expand_home(path: &Path) -> PathBuf {
    let raw = path.to_string_lossy();
    if let Some(stripped) = raw.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home).join(stripped);
        }
    }
    path.to_path_buf()
}

fn is_supported_image(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| {
            SUPPORTED_EXTENSIONS
                .iter()
                .any(|allowed| ext.eq_ignore_ascii_case(allowed))
        })
        .unwrap_or(false)
}
