PYTHON ?= .uv-venv/bin/python

.PHONY: wheel-devel rust-prepare clean clean-all

wheel-devel:
	uv pip install --python $(PYTHON) build
	$(PYTHON) -m build --wheel

rust-prepare:
	cargo build --release --manifest-path galapix-prepare-rs/Cargo.toml

clean:
	rm -rf build dist .pytest_cache .mypy_cache
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	find . -maxdepth 1 -type d -name '*.egg-info' -prune -exec rm -rf {} +

clean-all: clean
	rm -rf .uv-venv .venv
