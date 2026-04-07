#!/home/daniel/.pyenv/versions/gvmm-py3/bin/python3

from __future__ import annotations

import argparse
import configparser
import os
import socket
import sys


DEFAULT_CFG = os.path.join(os.environ["HOME"], ".galapix", "galapix.cfg")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CFG)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "-r",
        "--restart",
        dest="restart",
        nargs="?",
        const="all",
        help="restart a named resource, or all when no name is provided",
    )
    group.add_argument(
        "-k",
        "--kill",
        dest="kill",
        nargs="?",
        const="all",
        help="kill a named resource, or all when no name is provided",
    )
    return parser


def load_pixsock(cfg_path: str) -> str:
    config = configparser.ConfigParser()
    if not config.read(cfg_path):
        raise FileNotFoundError(cfg_path)

    pixsock = config.defaults().get("pixsock")
    if not pixsock:
        raise KeyError("pixsock")

    return pixsock


def send_command(pixsock: str, message: str) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(pixsock)
        sock.sendall(message.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        pixsock = load_pixsock(args.config)
        if args.restart is not None:
            send_command(pixsock, f"{args.restart} restart")
        else:
            send_command(pixsock, f"{args.kill} kill")
    except (FileNotFoundError, KeyError) as exc:
        print(exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print(exc, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
