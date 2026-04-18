from __future__ import annotations

import argparse
from pathlib import Path

from .models import ViewerOptions


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


def main() -> int:
    parser = argparse.ArgumentParser(prog="galapix-view")
    parser.add_argument("-d", "--database", default=str(Path.home() / ".galapix-py"))
    parser.add_argument("-t", "--threads", type=int, default=4)
    parser.add_argument("-p", "--pattern", action="append", default=[])
    parser.add_argument("--ignore-pattern-case", action="store_true")
    parser.add_argument("--validate-render", action="store_true")
    parser.add_argument("--validation-timeout", type=float, default=5.0)
    parser.add_argument("-r", "--title", default="galapix-py")
    parser.add_argument("-g", "--geometry", default="1280x720")
    parser.add_argument("-f", "--fullscreen", action="store_true")
    parser.add_argument("--sort", choices=("name", "name-reverse", "mtime", "mtime-reverse", "url", "url-reverse"))
    parser.add_argument("--images-per-row", type=int, default=None)
    parser.add_argument("--spacing", type=int, default=1)
    parser.add_argument("--background-color", type=parse_background_color, default=None)
    parser.add_argument("--selection-border-color", type=parse_background_color, default=None)
    parser.add_argument("--memory-only", action="store_true")
    parser.add_argument("--case-insensitive-sort", action="store_true")
    parser.add_argument("--show-filenames", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    width, height = parse_geometry(args.geometry)

    from .app import GalapixApp

    options = ViewerOptions(
        database=Path(args.database).expanduser(),
        threads=args.threads,
        ignore_pattern_case=args.ignore_pattern_case,
        title=args.title,
        width=width,
        height=height,
        fullscreen=args.fullscreen,
        sort=args.sort,
        images_per_row=args.images_per_row,
        spacing=max(1, args.spacing),
        background_color=args.background_color,
        selection_border_color=args.selection_border_color,
        case_insensitive_sort=args.case_insensitive_sort,
        show_filenames=args.show_filenames,
        memory_only=args.memory_only,
        validate_render=args.validate_render,
        validation_timeout=args.validation_timeout,
    )
    app = GalapixApp(options)
    try:
        app.view(args.paths, patterns=args.pattern)
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
