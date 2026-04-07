from __future__ import annotations

import argparse
from pathlib import Path

from .models import ViewerOptions


def main() -> int:
    parser = argparse.ArgumentParser(prog="galapix-prepare")
    parser.add_argument("-d", "--database", default=str(Path.home() / ".galapix-py"))
    parser.add_argument("-t", "--threads", type=int, default=4)
    parser.add_argument("-p", "--pattern", action="append", default=[])
    parser.add_argument("--ignore-pattern-case", action="store_true")
    parser.add_argument("--jpeg-quality", type=int, default=85)
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()

    from .app import GalapixApp

    options = ViewerOptions(
        database=Path(args.database).expanduser(),
        threads=args.threads,
        jpeg_quality=max(1, min(100, args.jpeg_quality)),
        ignore_pattern_case=args.ignore_pattern_case,
    )
    try:
        return 0 if GalapixApp(options).prepare(args.paths, patterns=args.pattern) else 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
