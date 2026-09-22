#!/usr/bin/env python3
"""Measure deterministic static-analysis scale on a generated corpus."""
from __future__ import annotations

import argparse
import json
import tempfile
import time
import tracemalloc
from pathlib import Path

from orbitkb.analysis.engine import StaticAnalysisEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark static analysis on generated Go handlers.")
    parser.add_argument("--files", type=int, default=500, help="Generated files (default: 500)")
    args = parser.parse_args()
    if args.files < 1:
        parser.error("--files must be positive")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for index in range(args.files):
            (root / f"handler_{index}.go").write_text(
                f"package main\nfunc handle{index}() {{ service.Process({index}) }}\n", encoding="utf-8"
            )
        tracemalloc.start()
        started = time.perf_counter()
        result = StaticAnalysisEngine().analyze(root, "go")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    print(json.dumps({"files": args.files, "edges": len(result.edges), "elapsed_ms": elapsed_ms, "peak_memory_bytes": peak}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
