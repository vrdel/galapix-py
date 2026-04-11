# galapix-py

Python port of `galapix` built around:

- `Pillow` for image decode, scaling, tile generation, and label rasterization
- `PyOpenGL` for textured tile rendering
- `PySDL2` for windowing and input
- `sqlite3` for persistent file and tile caches

The code mirrors the original C++ architecture:

```text
GalapixApp
  -> Viewer / SDLViewer
  -> Workspace
  -> Image
  -> ImageTileCache
  -> TileProvider
  -> DatabaseThread
  -> JobManager
  -> TileGenerator
  -> Database
```

## Commands

### Standalone entrypoints

After installation, these commands are available directly:

```bash
galapix-view [options] <paths...>
galapix-prepare [options] <paths...>
galapix-clean [options] [paths...]
```

### Subcommand entrypoint

The `galapix-py` command groups all functionality under subcommands:

```bash
galapix-py view [options] <paths...>
galapix-py prepare <paths...>
galapix-py selfcheck <paths...>
galapix-py list
galapix-py check
galapix-py cleanup [paths...]
```

### Global options

Available on all commands:

- `-d`, `--database`: cache root, default `~/.galapix-py`
- `-t`, `--threads`: worker count for prepare / background jobs
- `-p`, `--pattern`: regex path filter, can be passed multiple times
- `--ignore-pattern-case`: make `--pattern` filters case-insensitive

### View options

Available on `galapix-view` and `galapix-py view`:

- `-r`, `--title`: window title
- `-g`, `--geometry WxH`: initial window size
- `-f`, `--fullscreen`: start fullscreen
- `--sort {name,name-reverse,mtime,mtime-reverse}`: startup ordering for direct image views
- `--images-per-row N`: wrap after `N` images; default is auto-wrap into a square-ish grid
- `--spacing N`: row spacing multiplier, where `1` is the default gap
- `--case-insensitive-sort`: make name sorting (key `1`) case-insensitive
- `--background-color RRGGBB`: hex background color (e.g. `4b5262`)
- `--selection-border-color RRGGBB`: hex selection outline color (e.g. `B02A37`)
- `--memory-only`: bypass the SQLite tile cache and generate tiles in memory
- `--show-filenames`: draw filename labels above visible images
- `--validate-render`: exit after the first textured frame in a live desktop session
- `--validation-timeout`: render validation timeout in seconds

### Prepare options

Available on `galapix-prepare` and `galapix-py prepare`:

- `--jpeg-quality N`: JPEG quality for cached tiles (default 85)

### Notes

- `view` accepts image paths, directories, and saved workspace files ending in `.galapix`
- `prepare` builds the full tile pyramid into the SQLite cache
- `cleanup` / `galapix-clean` remove the whole cache if no paths are provided, or only matching cached images if paths/directories are provided
- `list` prints cached image URLs
- `check` reports whether cached file entries still match files on disk

## Install

```bash
pip install -e .
```

System dependencies are still required for:

- OpenGL
- SDL2

In this repo, the tested setup path is:

```bash
uv venv .uv-venv
uv pip install --python .uv-venv/bin/python -e .
```

To build an installable wheel package:

```bash
make wheel-devel
```

The wheel is written to `dist/`.

For a non-GUI smoke test of the core pipeline:

```bash
.uv-venv/bin/python -m galapix_py.cli selfcheck \
  /home/daniel/my_work/git.galapix-ont-vrdel/galapix-ont/test/software_surface_test.jpg
```

For a live desktop render validation run that exits automatically after the
first textured frame:

```bash
.uv-venv/bin/python -m galapix_py.cli --validate-render view /path/to/image.jpg
```

Typical prepare run:

```bash
galapix-prepare /path/to/images
```

Typical view run:

```bash
galapix-view --sort name --show-filenames /path/to/images
```

## Viewer Controls

Navigation:

- `Mouse wheel`: zoom around cursor
- `Left drag`: pan
- `Arrow keys`: pan
- `Ctrl+Arrow keys`: faster pan
- `w`: zoom in
- `s`: zoom out
- `Ctrl+w` / `Ctrl+s`: faster zoom
- `h`: reset view
- `x`: zoom to selection, or whole workspace if nothing is selected
- `n`: zoom to original size (1:1 pixel mapping) centered on selected image

Selection and editing:

- `Left click`: select topmost image under cursor
- `/`: open live filename search; typing filters visible images immediately
    - `Backspace`: delete one search character while the search box is open
    - `Enter`: close the search box and keep the current filter
    - `Esc`: close the search box and clear the current filter
- `i`: isolate current selection
- `Delete`: delete current selection from the workspace
- `F5`: refresh selected images from disk / database

Layout and ordering:

- `1`: sort by URL and relayout
- `Shift+1`: reverse sort by URL and relayout
- `2`: sort by file mtime and relayout
- `Shift+2`: reverse sort by file mtime and relayout

Display and debug:

- `b`: cycle background color forward
- `Shift+b`: cycle background color backward
- `F1`: toggle status overlay in the window title
- `c`: clear CPU tile caches and OpenGL textures
- `Space`: print visible image URLs to the terminal
- `0`: print workspace/runtime info to the terminal

Workspace persistence:

- `F2`: load `/tmp/workspace-dump.galapix`
- `F3`: save `/tmp/workspace-dump.galapix`

Quit:

- `Esc`: exit viewer

## Notes

- Tiles are stored as JPEG blobs in SQLite.
- Cached tile JPEG quality is `Q=85` with metadata stripped.
- `prepare` now prints a summary including discovered files, skipped files, prepared files, stored tile count, and elapsed time.
- The renderer supports:
  - exact tile rendering
  - higher-resolution child-tile fallback
  - lower-resolution parent-tile fallback
  - proactive coarse parent requests
  - scale-aware cache pruning
  - cancellable in-flight tile requests
- The viewer supports:
  - startup sorting by file name or mtime
  - centered multi-row initial layouts
  - configurable row spacing and row limits
  - selection
  - selection-aware zoom
  - workspace save/load
  - background cycling
  - title-based status overlay
  - optional filename labels above images
  - live render validation mode
