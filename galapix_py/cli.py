from __future__ import annotations

import argparse
from pathlib import Path

from .models import ViewerOptions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="galapix-py")
    parser.add_argument("-d", "--database", default=str(Path.home() / ".galapix-py"))
    parser.add_argument("-t", "--threads", type=int, default=4)
    parser.add_argument("-p", "--pattern", action="append", default=[])
    parser.add_argument("--ignore-pattern-case", action="store_true")
    parser.add_argument("--validate-render", action="store_true")
    parser.add_argument("--validation-timeout", type=float, default=5.0)

    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("view", "prepare", "selfcheck"):
        cmd = sub.add_parser(name)
        if name == "view":
            cmd.add_argument("-r", "--title", default=argparse.SUPPRESS)
            cmd.add_argument("-g", "--geometry", default="1280x720")
            cmd.add_argument("-f", "--fullscreen", action="store_true")
            cmd.add_argument("--sort", choices=("name", "name-reverse", "mtime", "mtime-reverse"))
            cmd.add_argument("--images-per-row", type=int, default=None)
            cmd.add_argument("--spacing", type=int, default=1)
            cmd.add_argument("--background-color", type=parse_background_color, default=None)
            cmd.add_argument("--selection-border-color", type=parse_background_color, default=None)
            cmd.add_argument("--memory-only", action="store_true")
            cmd.add_argument("--show-filenames", action="store_true")
        elif name == "prepare":
            cmd.add_argument("--jpeg-quality", type=int, default=85)
        cmd.add_argument("paths", nargs="*")

    sub.add_parser("list")
    sub.add_parser("check")
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("paths", nargs="*")
    return parser


def parse_geometry(text: str) -> tuple[int, int]:
    width, height = text.lower().split("x", 1)
    return int(width), int(height)


def parse_background_color(text: str) -> tuple[float, float, float, float]:
    value = text.strip()
    if value.startswith("#"):
        value = value[1:]
    if len(value) != 6:
        raise argparse.ArgumentTypeError("background color must be a 6-digit hex color like #263238")
    try:
        red = int(value[0:2], 16)
        green = int(value[2:4], 16)
        blue = int(value[4:6], 16)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("background color must be a valid hexadecimal color") from exc
    return red / 255.0, green / 255.0, blue / 255.0, 1.0


def _run_command(app, args) -> None:
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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    geometry = getattr(args, "geometry", "1280x720")
    width, height = parse_geometry(geometry)
    from .app import GalapixApp

    options = ViewerOptions(
        database=Path(args.database).expanduser(),
        threads=args.threads,
        jpeg_quality=max(1, min(100, getattr(args, "jpeg_quality", 85))),
        ignore_pattern_case=args.ignore_pattern_case,
        title=getattr(args, "title", "galapix-py"),
        width=width,
        height=height,
        fullscreen=getattr(args, "fullscreen", False),
        sort=getattr(args, "sort", None),
        images_per_row=getattr(args, "images_per_row", None),
        spacing=max(1, getattr(args, "spacing", 1)),
        background_color=getattr(args, "background_color", None),
        selection_border_color=getattr(args, "selection_border_color", None),
        show_filenames=getattr(args, "show_filenames", False),
        memory_only=getattr(args, "memory_only", False),
        validate_render=args.validate_render,
        validation_timeout=args.validation_timeout,
    )
    app = GalapixApp(options)
    try:
        _run_command(app, args)
    except KeyboardInterrupt:
        return


def cleanup_main() -> None:
    parser = argparse.ArgumentParser(prog="galapix-clean")
    parser.add_argument("-d", "--database", default=str(Path.home() / ".galapix-py"))
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    from .app import GalapixApp

    options = ViewerOptions(database=Path(args.database).expanduser())
    try:
        GalapixApp(options).cleanup(args.paths)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
