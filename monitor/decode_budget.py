"""Per-token decode budget from a DeepSeek V4.1 engine timeline.

When the engine runs with ``DS4_ARGODRIVE_TIMELINE`` it writes ``timeline.csv``
into the run directory: one row per position, the first data row being the
prefill (``pos`` equals the prompt length) and every later row one generated
token. This module reduces that file to the six-way split the decode-budget
work reads a token by -- GPU command-buffer span, expert misses (pread wait),
the head, Engram, expert prepare/install, and the residual (CPU/GPU bubbles
plus the final drain) -- as per-token means in milliseconds.

Pure functions, standard library only, so the dashboard, the harness and the
tests can use it without the server running. Nothing here raises for a missing
or damaged file: the caller gets ``None`` and decides what to show. A run that
was recorded without a timeline has no budget, never a budget of zeros.

    python3 decode_budget.py /path/to/run-dir     # prints the one-line summary
"""
from __future__ import annotations

import csv
import math
import os
import sys
from pathlib import Path
from typing import Callable, Optional, Union

RunDir = Union[str, "os.PathLike[str]"]
Budget = dict[str, Optional[float]]

TIMELINE_FILE = "timeline.csv"
BENCH_FILE = "bench.csv"
# The prefill row plus the first two generated tokens are left out: the prefill
# is not a decode step, and the first tokens after it still run on cold caches.
SKIP_ROWS = 3
# Budget segments in stacking order; ``other`` is the residual of ``total``.
SEGMENTS: tuple[str, ...] = ("gpu", "misses", "head", "engram", "prepare", "other")
SEGMENT_LABELS: dict[str, str] = {
    "gpu": "GPU", "misses": "misses", "head": "head",
    "engram": "Engram", "prepare": "prepare", "other": "other",
}
# Timeline columns each reported mean is built from (summed per row).
_COLUMNS: dict[str, tuple[str, ...]] = {
    "gpu": ("gpu_cb_span_ms",),
    "misses": ("expert_pread_ms",),
    "head": ("logits_ms",),
    "engram": ("engram_ms",),
    "prepare": ("expert_prepare_ms", "expert_install_ms"),
    "total": ("total_ms",),
    "cbs_per_token": ("cb_count",),
    "misses_per_token": ("missing_experts",),
}
_REQUIRED: frozenset[str] = frozenset(c for cols in _COLUMNS.values() for c in cols)
_BENCH_COLUMNS: tuple[tuple[str, str], ...] = (
    ("steady_tok_s", "gen_steady_tps"), ("prefill_tok_s", "prefill_tps"),
)


def _finite(value: Optional[str]) -> Optional[float]:
    """``value`` as a finite float, or ``None`` for anything that is not one."""
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _timeline_rows(path: Path) -> list[dict[str, float]]:
    """Well-formed data rows of a timeline, in file order.

    Comment lines (``#`` prefix) and blank lines are ignored. A row is kept only
    when every column the budget needs parses as a finite number; anything else
    (a truncated last line, text where a number should be, a short row) is
    skipped rather than allowed to poison a mean. An unreadable file or one
    without the timeline header yields no rows.
    """
    rows: list[dict[str, float]] = []
    try:
        with path.open(newline="", errors="replace") as fh:
            lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
        reader = csv.DictReader(lines)
        if not reader.fieldnames or not _REQUIRED.issubset(reader.fieldnames):
            return []
        for raw in reader:
            parsed = {column: _finite(raw.get(column)) for column in _REQUIRED}
            if all(v is not None for v in parsed.values()):
                rows.append({k: v for k, v in parsed.items() if v is not None})
    except (OSError, csv.Error):
        return []
    return rows


def _bench_rates(path: Path) -> Budget:
    """``steady_tok_s`` and ``prefill_tok_s`` from the harness ``bench.csv``
    (its second line is the one data row); ``None`` for anything missing."""
    rates: Budget = {key: None for key, _ in _BENCH_COLUMNS}
    try:
        with path.open(newline="", errors="replace") as fh:
            row = next(csv.DictReader(fh), None)
    except (OSError, csv.Error):
        return rates
    if row:
        for key, column in _BENCH_COLUMNS:
            rates[key] = _finite(row.get(column))
    return rates


def decode_budget(run_dir: RunDir) -> Optional[Budget]:
    """Mean per-token decode budget of one run directory, or ``None``.

    Reads ``<run_dir>/timeline.csv``, drops the prefill row and the first two
    generated tokens (``SKIP_ROWS``), and averages the rest. Returns ``None``
    when the file is missing, unreadable, lacks the timeline columns, or leaves
    no token to average after the skip.

    Keys (milliseconds per token unless stated): ``gpu`` (GPU command-buffer
    span), ``misses`` (expert pread wait), ``head`` (logits), ``engram``,
    ``prepare`` (expert prepare + install), ``other`` (what is left of
    ``total`` after those five, clamped at zero: CPU/GPU bubbles and the final
    drain -- so the six segments add up to ``total``), ``total``; ``tokens``
    (rows averaged), ``cbs_per_token`` (mean command buffers),
    ``misses_per_token`` (mean missing experts); and, from ``bench.csv`` when
    it is present and readable, ``steady_tok_s`` and ``prefill_tok_s``
    (``None`` otherwise). Means are rounded to 3 decimals.
    """
    directory = Path(run_dir)
    rows = _timeline_rows(directory / TIMELINE_FILE)[SKIP_ROWS:]
    if not rows:
        return None
    mean: Callable[[str], float] = lambda key: sum(row[c] for row in rows for c in _COLUMNS[key]) / len(rows)
    budget: Budget = {key: mean(key) for key in _COLUMNS}
    parts = sum(budget[k] or 0.0 for k in SEGMENTS if k != "other")
    budget["other"] = max(0.0, (budget["total"] or 0.0) - parts)
    budget = {k: round(v, 3) for k, v in budget.items() if v is not None}
    budget["tokens"] = len(rows)
    budget.update(_bench_rates(directory / BENCH_FILE))
    return budget


def format_budget(budget: Budget) -> str:
    """One line for a log or a terminal: the steady rate, the per-token totals,
    then each segment as milliseconds and its share of the token."""
    total = float(budget.get("total") or 0.0)

    def segment(key: str) -> str:
        value = float(budget.get(key) or 0.0)
        share = 100.0 * value / total if total > 0 else 0.0
        return f"{SEGMENT_LABELS[key]} {value:.1f} ms {share:.0f}%"

    steady = budget.get("steady_tok_s")
    parts = [f"{steady:.2f} tok/s steady" if steady is not None else "steady rate not recorded",
             f"{total:.1f} ms/token",
             f"{float(budget.get('cbs_per_token') or 0.0):.1f} command buffers/token",
             f"{float(budget.get('misses_per_token') or 0.0):.1f} misses/token"]
    parts += [segment(key) for key in SEGMENTS]
    parts.append(f"{int(budget.get('tokens') or 0)} tokens")
    return " · ".join(parts)


def main(argv: list[str]) -> int:
    """``decode_budget.py RUN_DIR``: print the one-line summary; 1 when there is no budget."""
    if len(argv) != 2:
        print("usage: decode_budget.py RUN_DIR", file=sys.stderr)
        return 2
    budget = decode_budget(argv[1])
    if budget is None:
        print(f"no usable {TIMELINE_FILE} in {argv[1]}", file=sys.stderr)
        return 1
    print(format_budget(budget))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
