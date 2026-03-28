# galapix-prepare-rs

Native/offline cache builder for the `galapix-py` SQLite cache format.

Current status:
- CLI implemented
- path expansion implemented
- SQLite schema compatibility implemented
- skip-unchanged detection implemented
- native `libvips` tile-generation backend implemented
- threaded prepare pipeline implemented
- batched SQLite tile writes implemented

Implementation notes:
- uses handwritten FFI against the local `libvips` C API
- reproduces the current Python prepare path:
  - load source image with random access
  - for each scale compute `ceil(width / 2^scale)` / `ceil(height / 2^scale)`
  - call `thumbnail_image(..., size="force")`
  - crop into `256x256` tiles
  - save each tile as JPEG with `Q=85, strip=True`
- workers generate tiles in parallel per image
- one writer path stores file rows and tile batches into SQLite

Example:

```bash
cargo run -- -d /tmp/galapix-rs-cache /path/to/images
```
