#!/usr/bin/env python3
"""Print a reproducible local static-analysis scale profile as JSON."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.scale import run_scale_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile static analysis on generated Go handlers.")
    parser.add_argument("--files", type=int, nargs="+", default=[100, 500, 1000], help="Generated file counts")
    parser.add_argument("--repeat", type=int, default=3, help="Runs per file count (default: 3)")
    args = parser.parse_args()
    try:
        profile = run_scale_profile(tuple(args.files), args.repeat)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(profile.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
