#!/usr/bin/env python3
"""Run an arbitrary command inside the galapix-py pyenv environment."""

import os
import subprocess
import sys


PYENV_ENV = "galapix-py"


def main() -> int:
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <command> [args...]", file=sys.stderr)
        return 1

    pyenv_root = os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
    venv_bin = os.path.join(pyenv_root, "versions", PYENV_ENV, "bin")

    if not os.path.isdir(venv_bin):
        print(f"pyenv environment not found: {venv_bin}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["PYENV_ROOT"] = pyenv_root
    env["VIRTUAL_ENV"] = os.path.join(pyenv_root, "versions", PYENV_ENV)
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONHOME", None)

    result = subprocess.run(sys.argv[1:], env=env)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
