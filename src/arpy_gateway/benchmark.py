"""Reproducible store benchmark; deliberately does not require Qt or a display."""
from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

from .studio.store import View, apply_grid, auto_number, count_rows, initialize, load_page, seed_demo


def run(rows: int = 100_000) -> dict[str, float]:
    results: dict[str, float] = {}
    with tempfile.TemporaryDirectory(prefix="arpy_bench_") as folder:
        db = Path(folder) / "bench.sqlite"
        initialize(db)
        def measured(name, func):
            t0 = time.perf_counter()
            value = func()
            results[name + "_seconds"] = round(time.perf_counter() - t0, 4)
            return value
        assert measured("seed", lambda: seed_demo(db, rows)) == rows
        assert measured("count", lambda: count_rows(db, View())) == rows
        page = measured("page_near_end", lambda: load_page(db, View(), max(0, (rows - 1) // 256)))
        assert page
        filtered = measured("filter", lambda: count_rows(db, View(search="Voltage")))
        assert filtered > 0
        measured("sorted_page", lambda: load_page(db, View(sort_field="name", descending=True), 0))
        amount = min(10000, rows)
        measured("auto_number_10k", lambda: auto_number(db, View(), 0, amount - 1, "target_address", 10000, 1))
        measured("paste_1k", lambda: apply_grid(db, View(), 0, 10, [["kW"]] * min(1000, rows)))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    args = parser.parse_args()
    for key, value in run(args.rows).items():
        print(f"{key:28} {value:>10}")


if __name__ == "__main__":
    main()
