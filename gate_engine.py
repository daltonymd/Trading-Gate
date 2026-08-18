#!/usr/bin/env python3
"""Run the gate against replay data.

    python gate_engine.py                 # full scenario, ends ARMED and takes the trade
    python gate_engine.py --no-orderflow  # same bars, no footprint: never arms
    python gate_engine.py --thin-volume   # participation below the MNQ floor: halted
    python gate_engine.py --instrument ES # threshold not calibrated: halted, not inherited
    python gate_engine.py --json          # print the API payload instead of the screen
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from tradinggate import (
    AUCTION_ABSORPTION_V1,
    Direction,
    EventLogger,
    GammaRegime,
    GateEngine,
    GateState,
    JsonStore,
    PreMarketPlan,
    ReplayProvider,
    SessionState,
    Settings,
    StaticOptionsProvider,
    StrippedProvider,
    Structure,
    ViolationKind,
    behaviour,
    explain,
    gate_payload,
    get_profile,
    render,
)
from tradinggate.marketdata import GexSnapshot
from tradinggate.replay_builder import (
    CONFIRMING_BAR,
    SESSION_OPEN_BAR,
    mnq_long_scenario,
    session_open_time,
    thin_participation_tail,
    write_jsonl,
)

DATA = Path(__file__).parent / "data"
REPLAY = DATA / "replay" / "mnq_long_absorption.jsonl"


def build_plan(instrument: str, session_date: str, at: datetime) -> PreMarketPlan:
    plan = PreMarketPlan(
        instrument=instrument,
        session_date=session_date,
        direction=Direction.LONG,
        htf_structure=Structure.VALUE_UP,
        gamma_regime=GammaRegime.NEGATIVE_GAMMA,
        call_wall=20200.0,
        put_wall=19800.0,
        gamma_flip=20000.0,
        scenarios=[
            "Pullback into discount below value; long on a second seller failure.",
            "Acceptance back inside value without a failure: stand aside.",
        ],
        invalidation_note="Close through the 0.886 of the impulse leg.",
        trail_plan="Trail behind each 5m close whose aggression produced progression.",
    )
    plan.lock(at)
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description="Trading execution gate")
    ap.add_argument("--instrument", default=None)
    ap.add_argument("--replay", default=None, help="path to a JSONL bar file")
    ap.add_argument("--no-orderflow", action="store_true",
                    help="strip footprint data to prove the gate will not arm on OHLC")
    ap.add_argument("--thin-volume", action="store_true",
                    help="drop the confirming bar below the participation floor")
    ap.add_argument("--json", action="store_true", help="print the API payload")
    ap.add_argument("--quiet", action="store_true", help="only the final screen")
    args = ap.parse_args()

    settings = Settings.from_env()
    symbol = (args.instrument or settings.default_instrument).upper()
    profile = get_profile(symbol)

    bars = mnq_long_scenario()
    if args.thin_volume:
        bars = thin_participation_tail(bars, CONFIRMING_BAR)
    if not args.replay:
        write_jsonl(bars, REPLAY)
        provider = ReplayProvider.from_bars(bars, symbol, profile.confirmation_timeframe)
    else:
        provider = ReplayProvider(args.replay, symbol, profile.confirmation_timeframe)
        bars = provider._all
    if args.no_orderflow:
        provider = StrippedProvider(provider)

    open_time = session_open_time(bars)
    session_date = open_time.date().isoformat()
    session = SessionState(
        instrument=symbol,
        session_date=session_date,
        open_time=open_time,
        limit_minutes=profile.session_limit_minutes,
        max_consecutive_losses=profile.max_consecutive_losses,
    )
    plan = build_plan(symbol, session_date, open_time)
    logger = EventLogger(sink=DATA / "events" / f"{symbol}_{session_date}.jsonl",
                         echo=not args.quiet)

    engine = GateEngine(
        instrument=profile,
        plan=plan,
        session=session,
        data=provider,
        strategy=AUCTION_ABSORPTION_V1,
        options=StaticOptionsProvider(GexSnapshot(
            regime=GammaRegime.NEGATIVE_GAMMA, call_wall=20200.0,
            put_wall=19800.0, gamma_flip=20000.0, source="manual-premarket")),
        logger=logger,
    )

    print(f"\nmode {settings.trading_mode.value}   instrument {symbol}   "
          f"strategy {AUCTION_ABSORPTION_V1.key}")
    print(f"participation floor: "
          f"{profile.minimum_volume if profile.volume_calibrated else 'NOT CALIBRATED'}")
    print(f"replay bars: {len(bars)}   NY open at bar {SESSION_OPEN_BAR}\n")

    inner = provider.inner if isinstance(provider, StrippedProvider) else provider
    snapshot = None
    trade = None
    entry_snapshot = None

    while not inner.exhausted:
        inner.advance()
        snapshot = engine.evaluate()
        if snapshot.gate_state is GateState.ARMED and trade is None:
            entry_snapshot = snapshot
            trade = engine.enter(snapshot, size=1)

    if snapshot is None:
        print("no bars")
        return 1

    if entry_snapshot is not None:
        print("\n=== the moment the gate armed ===")
        print(render(entry_snapshot))
        print()
        print(explain(entry_snapshot))

    print("\n=== final bar ===")
    print(render(snapshot))
    print()
    print(explain(snapshot))

    if trade is not None:
        # Managed exit: the follow-through bar reclaims value, so the trade is
        # carried to the swing target rather than cut at the checkpoint.
        exit_price = bars[-1].close
        grade = engine.close(trade, exit_price)
        print("\n--- trade result ---")
        print(grade.render())
        print("\nreasons:")
        for reason in grade.reasons:
            print(f"  - {reason}")
    else:
        print("\nNo trade taken. That is a result, not a failure - "
              f"{session.rejected_setups} setup(s) rejected, "
              f"{session.gate_attempts} evaluations.")

    print("\n--- session health ---")
    print(json.dumps(session.health(), indent=2, default=str))

    if args.json:
        print("\n--- api payload ---")
        print(json.dumps(gate_payload(snapshot, session), indent=2, default=str))

    store = JsonStore(DATA)
    key = store.save_session(session)
    for t in session.trades:
        store.save_trade(key, t)
    store.save_events(key, logger.events)

    print("\n--- behaviour analytics ---")
    for row in behaviour.analyse(session.trades):
        print(" ", json.dumps(row, default=str))
    print(f"\nsaved to {DATA / 'sessions' / (key + '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
