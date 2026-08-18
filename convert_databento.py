"""
Convert Databento CME Trades CSV into the footprint CSV format
expected by tradinggate/loaders.py.

Input:
    Databento Trades CSV

Output:
    ts,open,high,low,close,volume,price,bid_volume,ask_volume

Mapping:
    side B -> aggressive buyer -> ask_volume
    side A -> aggressive seller -> bid_volume
    side N -> unclassified (not assigned to either side)

Default bar interval: 5 minutes.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def parse_timestamp(value: str) -> datetime:
    """
    Databento timestamps can contain nanoseconds.
    Python datetime supports microseconds, so truncate fractional
    seconds to six digits.
    """
    value = value.strip()

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    if "." in value:
        main, rest = value.split(".", 1)

        if "+" in rest:
            fraction, offset = rest.split("+", 1)
            fraction = fraction[:6].ljust(6, "0")
            value = f"{main}.{fraction}+{offset}"

        elif "-" in rest:
            fraction, offset = rest.rsplit("-", 1)
            fraction = fraction[:6].ljust(6, "0")
            value = f"{main}.{fraction}-{offset}"

    return datetime.fromisoformat(value)


def floor_bar_time(ts: datetime, minutes: int) -> datetime:
    """Floor timestamp to the beginning of its bar."""
    minute = (ts.minute // minutes) * minutes

    return ts.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def iso_utc(ts: datetime) -> str:
    """Return timestamp in UTC ISO-8601 format."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def convert(input_file: Path, output_file: Path, bar_minutes: int = 5) -> None:

    bars = {}

    total_rows = 0
    used_rows = 0
    buyer_volume = 0
    seller_volume = 0
    unknown_volume = 0

    symbols = set()

    print(f"Reading: {input_file}")
    print(f"Bar interval: {bar_minutes} minutes")
    print()

    with input_file.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as fh:

        reader = csv.DictReader(fh)

        required = {
            "ts_event",
            "action",
            "side",
            "price",
            "size",
        }

        missing = required - set(reader.fieldnames or [])

        if missing:
            raise ValueError(
                "Input CSV is missing required columns: "
                + ", ".join(sorted(missing))
            )

        for row in reader:

            total_rows += 1

            # Only process trade events.
            if row["action"] != "T":
                continue

            try:
                ts = parse_timestamp(row["ts_event"])
                price = float(row["price"])
                size = int(float(row["size"]))
            except (ValueError, TypeError):
                continue

            if size <= 0:
                continue

            side = row["side"].strip().upper()

            symbol = row.get("symbol", "").strip()

            if symbol:
                symbols.add(symbol)

            # Only use the September 2026 MNQ outright contract.
            # Do not mix deferred contracts or calendar spreads.
            if symbol != "MNQU6":
                continue

            bar_ts = floor_bar_time(ts, bar_minutes)

            if bar_ts not in bars:

                bars[bar_ts] = {
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 0,
                    "prices": defaultdict(
                        lambda: {
                            "bid": 0,
                            "ask": 0,
                        }
                    ),
                }

            bar = bars[bar_ts]

            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["close"] = price
            bar["volume"] += size

            if side == "B":

                # Aggressive buyer lifted the ask.
                bar["prices"][price]["ask"] += size
                buyer_volume += size

            elif side == "A":

                # Aggressive seller hit the bid.
                bar["prices"][price]["bid"] += size
                seller_volume += size

            else:

                # Do NOT fabricate direction.
                unknown_volume += size

            used_rows += 1

            if total_rows % 500_000 == 0:
                print(
                    f"Processed {total_rows:,} rows..."
                )

    if not bars:
        raise ValueError("No usable trade records were found.")

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_rows = 0

    with output_file.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as fh:

        writer = csv.writer(fh)

        writer.writerow(
            [
                "ts",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "price",
                "bid_volume",
                "ask_volume",
            ]
        )

        for bar_ts in sorted(bars):

            bar = bars[bar_ts]

            for price in sorted(bar["prices"]):

                level = bar["prices"][price]

                writer.writerow(
                    [
                        iso_utc(bar_ts),
                        bar["open"],
                        bar["high"],
                        bar["low"],
                        bar["close"],
                        bar["volume"],
                        price,
                        level["bid"],
                        level["ask"],
                    ]
                )

                output_rows += 1

    classified_volume = buyer_volume + seller_volume
    total_volume = classified_volume + unknown_volume

    if total_volume:
        classified_pct = classified_volume / total_volume * 100
    else:
        classified_pct = 0.0

    print()
    print("=== CONVERSION COMPLETE ===")
    print()
    print(f"Input rows:          {total_rows:,}")
    print(f"Trade rows used:     {used_rows:,}")
    print(f"5-minute bars:       {len(bars):,}")
    print(f"Footprint rows:      {output_rows:,}")
    print()
    print(f"Aggressive buy vol:  {buyer_volume:,}")
    print(f"Aggressive sell vol: {seller_volume:,}")
    print(f"Unknown-side vol:    {unknown_volume:,}")
    print(f"Classified volume:   {classified_pct:.2f}%")

    if symbols:
        print()
        print("Symbols found:")
        for symbol in sorted(symbols):
            print(f"  {symbol}")

    print()
    print(f"Output:")
    print(output_file)


def main() -> int:

    parser = argparse.ArgumentParser(
        description="Convert Databento Trades CSV to footprint CSV"
    )

    parser.add_argument(
        "input",
        help="Databento Trades CSV",
    )

    parser.add_argument(
        "output",
        help="Output footprint CSV",
    )

    parser.add_argument(
        "--minutes",
        type=int,
        default=5,
        help="bar duration in minutes (default: 5)",
    )

    args = parser.parse_args()

    input_file = Path(args.input)
    output_file = Path(args.output)

    if not input_file.exists():
        print(f"ERROR: input file not found: {input_file}")
        return 1

    convert(
        input_file=input_file,
        output_file=output_file,
        bar_minutes=args.minutes,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())