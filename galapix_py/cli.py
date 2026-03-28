from __future__ import annotations

import argparse
from pathlib import Path

from .models import ViewerOptions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="galapix-py")
    parser.add_argument("-d", "--database", default=str(Path.home() / ".galapix-py"))
    parser.add_argument("-t", "--threads", type=int, default=4)
    parser.add_argument("-g", "--geometry", default="1280x720")
    parser.add_argument("-f", "--fullscreen", action="store_true")
    parser.add_argument("--images-per-row", type=int, default=None)
    parser.add_argument("-p", "--pattern", action="append", default=[])
    parser.add_argument("-r", "--title", default="galapix-py")
    parser.add_argument("--memory-only", action="store_true")
    parser.add_argument("--validate-render", action="store_true")
    parser.add_argument("--validation-timeout", type=float, default=5.0)

    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("view", "prepare", "selfcheck"):
        cmd = sub.add_parser(name)
        if name == "view":
            cmd.add_argument("--show-filenames", action="store_true")
        cmd.add_argument("paths", nargs="*")

    sub.add_parser("list")
    sub.add_parser("check")
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("paths", nargs="*")
    return parser


def parse_geometry(text: str) -> tuple[int, int]:
    width, height = text.lower().split("x", 1)
    return int(width), int(height)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    width, height = parse_geometry(args.geometry)
    from .app import GalapixApp

    options = ViewerOptions(
        database=Path(args.database).expanduser(),
        threads=args.threads,
        title=args.title,
        width=width,
        height=height,
        fullscreen=args.fullscreen,
        images_per_row=args.images_per_row,
        show_filenames=getattr(args, "show_filenames", False),
        memory_only=args.memory_only,
        validate_render=args.validate_render,
        validation_timeout=args.validation_timeout,
    )
    app = GalapixApp(options)
    if args.command == "view":
        app.view(args.paths, patterns=args.pattern)
    elif args.command == "prepare":
        app.prepare(args.paths)
    elif args.command == "selfcheck":
        app.selfcheck(args.paths)
    elif args.command == "list":
        app.list_files()
    elif args.command == "check":
        app.check()
    elif args.command == "cleanup":
        app.cleanup(args.paths)


def cleanup_main() -> None:
    parser = argparse.ArgumentParser(prog="galapix-clean")
    parser.add_argument("-d", "--database", default=str(Path.home() / ".galapix-py"))
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    from .app import GalapixApp

    options = ViewerOptions(database=Path(args.database).expanduser())
    GalapixApp(options).cleanup(args.paths)


if __name__ == "__main__":
    main()
