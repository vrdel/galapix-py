# Session Notes

Current repo:

- `/home/daniel/my_work/git.galapix-py/galapix-py`

Recent commits:

- `0868f7e` `Add tests/`
- `b73c1ed` `Add build Makefile targets`

Current status:

- Python port of `galapix` is in this repo under `galapix_py/`
- non-GUI selfcheck exists and passes
- basic `tests/` suite exists and passes
- `Makefile` includes:
  - `wheel-devel`
  - `clean`
  - `clean-all`

Common commands:

```bash
uv venv .uv-venv
uv pip install --python .uv-venv/bin/python -e .
.uv-venv/bin/python -m unittest discover -s tests -v
.uv-venv/bin/python -m galapix_py.cli selfcheck /path/to/image.jpg
.uv-venv/bin/python -m galapix_py.cli view /path/to/image.jpg
make wheel-devel
make clean
make clean-all
```

Wheel output:

- `dist/galapix_py-0.1.0-py3-none-any.whl`

Likely next tasks:

- add more automated tests around database thread and tile fallback behavior
- improve viewer rendering validation in a live desktop session
- package/release polish if distribution is needed
