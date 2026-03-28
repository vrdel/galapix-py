# galapix-py

Python port of `galapix` built around:

- `pyvips` for image decode, scaling, and tile generation
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

```bash
galapix-py view <paths...>
galapix-py prepare <paths...>
galapix-py selfcheck <paths...>
galapix-py list
galapix-py check
galapix-py cleanup
```

`view` also accepts saved workspace files ending in `.galapix`.

## Install

```bash
pip install -e .
```

System dependencies are still required for:

- `libvips`
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

Selection and editing:

- `Left click`: select topmost image under cursor
- `i`: isolate current selection
- `Delete`: delete current selection from the workspace
- `F5`: refresh selected images from disk / database

Layout and ordering:

- `1`: sort by URL and relayout
- `Shift+1`: reverse sort by URL and relayout

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
- The implemented core is the main `view` and tile generation pipeline plus offline cache generation.
- The renderer supports:
  - exact tile rendering
  - higher-resolution child-tile fallback
  - lower-resolution parent-tile fallback
  - proactive coarse parent requests
  - scale-aware cache pruning
  - cancellable in-flight tile requests
- The viewer supports:
  - selection
  - selection-aware zoom
  - workspace save/load
  - background cycling
  - title-based status overlay
- Not implemented yet:
  - Zoomify provider
  - Mandelbrot / synthetic providers
  - GTK frontend
  - the original C++ tool system (move/resize/rotate/grid tools)
  - richer on-screen text overlay
