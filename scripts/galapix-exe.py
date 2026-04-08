#!/usr/bin/env python3
"""Run galapix commands inside the galapix-py pyenv environment."""

import argparse
import os
import subprocess
import sys


PYENV_ENV = "galapix-py"

COMMAND_MAP = {
    "view": "galapix-view",
    "prepare": "galapix-prepare",
    "clean": "galapix-clean",
}


def build_parser() -> argparse.ArgumentParser:
    commands = ", ".join(COMMAND_MAP)
    parser = argparse.ArgumentParser(
        prog="galapix-exe.py",
        description="Run galapix commands inside the galapix-py pyenv environment.",
        epilog=(
            f"shortcut commands:\n"
            f"  view       mapped to galapix-view\n"
            f"  prepare    mapped to galapix-prepare\n"
            f"  clean      mapped to galapix-clean\n"
            f"\n"
            f"Any other command is executed as-is within the environment.\n"
            f"\n"
            f"examples:\n"
            f"  %(prog)s view /path/to/images\n"
            f"  %(prog)s prepare -t 8 /path/to/images\n"
            f"  %(prog)s clean\n"
            f"  %(prog)s python -c \"import galapix_py\"\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--pyenv-env",
        default=PYENV_ENV,
        help=f"pyenv environment name (default: {PYENV_ENV})",
    )
    parser.add_argument(
        "command",
        metavar="COMMAND",
        help=f"shortcut ({commands}) or arbitrary executable",
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        metavar="ARGS",
        help="arguments passed to the command",
    )
    return parser


def main() -> int:
    parser = build_parser()
    parsed = parser.parse_args()

    pyenv_root = os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
    venv_bin = os.path.join(pyenv_root, "versions", parsed.pyenv_env, "bin")

    if not os.path.isdir(venv_bin):
        parser.error(f"pyenv environment not found: {venv_bin}")

    env = os.environ.copy()
    env["PYENV_ROOT"] = pyenv_root
    env["VIRTUAL_ENV"] = os.path.join(pyenv_root, "versions", parsed.pyenv_env)
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONHOME", None)

    mapped = COMMAND_MAP.get(parsed.command)
    executable = mapped if mapped is not None else parsed.command
    argv = [executable] + parsed.args

    try:
        result = subprocess.run(argv, env=env)
    except KeyboardInterrupt:
        return 130
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
