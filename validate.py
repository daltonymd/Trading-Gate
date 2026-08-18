#!/usr/bin/env python3
"""Historical validation.

    python validate.py --data /path/to/mnq            # real data (a file or a directory)
    python validate.py --data /path/to/mnq --ablation
    python validate.py --audit /path/to/mnq           # can this data answer C01-C05 at all?
    python validate.py --synthetic 30                 # harness smoke test ONLY

`--synthetic` runs against corpus.py. Its numbers describe the generator, not
the market, and the tool says so every time it prints them.
"""
from __future__ import annotations

import argparse
import json
from datetime import time
from pathlib import Path

from tradinggate import get_profile
from tradinggate.ablation import (
    format_ablation,
    override_impact,
    run_ablation,
    standard_variants,
    unverified_impact,
)
from tradinggate.backtest import diagnose_session, format_report, run_corpus
from tradinggate.simulator import FillModel


def parse_time(raw: str) -> time:
    hh, mm = raw.split(":")
    return time(int(hh), int(mm))


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate the gate on historical data")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", help="file or directory of bar JSONL / footprint CSV")
    src.add_argument("--synthetic", type=int, metavar="DAYS",
                     help="generated corpus - harness test only, not evidence")
    src.add_argument("--audit", help="report whether a dataset can answer C01-C05")
    ap.add_argument("--instrument", default="MNQ")
    ap.add_argument("--open", default="13:30", help="session open, UTC (HH:MM)")
    ap.add_argument("--ablation", action="store_true")
    ap.add_argument("--slippage-ticks", type=float, default=0.0)
    ap.add_argument("--commission-r", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--diagnose",
        action="store_true",
        help="show detailed diagnostics for candidate setups",
    )
    args = ap.parse_args()

    profile = get_profile(args.instrument)
    fills = FillModel(slippage_ticks=args.slippage_ticks,
                      commission_r=args.commission_r)

    if args.audit:
        from tradinggate.loaders import audit, load_bars_jsonl, load_footprint_csv
        path = Path(args.audit)
        files = sorted(path.glob("*")) if path.is_dir() else [path]
        bars = []
        for f in files:
            if f.suffix.lower() in (".jsonl", ".ndjson"):
                bars.extend(load_bars_jsonl(f))
            elif f.suffix.lower() == ".csv":
                bars.extend(load_footprint_csv(f))
        report = audit(bars)
        print(json.dumps(report, indent=2))
        if not report["usable_for_confirmation"]:
            print("\nThis data cannot answer C01-C05. The engine will report those "
                  "conditions as UNKNOWN and will not arm. That is correct "
                  "behaviour, not a bug - source bid x ask volume at price.")
        return 0

    synthetic = args.synthetic is not None
    if synthetic:
        from tradinggate.corpus import DISCLAIMER, build_corpus
        sessions = build_corpus(args.synthetic, args.instrument, args.seed)
        print("\n" + "!" * 72)
        print(DISCLAIMER)
        print("!" * 72)
    else:
        from tradinggate.loaders import load_sessions
        sessions = load_sessions(args.data, args.instrument, parse_time(args.open))
        if not sessions:
            print("No usable sessions. Check the session open time and the format.")
            return 1
    if args.diagnose:
        for session in sessions:
            diagnose_session(session, profile)
        return 0
    if not args.ablation:
        report = run_corpus(sessions, profile, fills=fills)
        print()
        print(format_report(report))

        trades = report.trades
        print("\nRULE AUDIT")
        for row in unverified_impact(trades):
            print("  unverified:", json.dumps(row))
        for row in override_impact(trades):
            print("  override:  ", json.dumps(row))

        if args.json:
            print("\n" + json.dumps(report.to_dict(), indent=2, default=str))
    else:
        rows = run_ablation(sessions, profile, standard_variants(), fills)
        print(f"\nABLATION over {len(sessions)} sessions")
        print()
        print(format_ablation(rows))
        if args.json:
            print("\n" + json.dumps([r.__dict__ for r in rows], indent=2, default=str))

    if synthetic:
        print("\n" + "!" * 72)
        print(DISCLAIMER)
        print("!" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
