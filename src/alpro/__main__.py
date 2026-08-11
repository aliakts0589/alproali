"""AL PRO CLI — `python -m alpro <command>`.

Commands:
  init-db     Create the database schema
  demo        Seed the demo portfolio (Ali's example)
  refresh     Run all data connectors (live where possible, labeled DEMO otherwise)
  briefing    Print the morning briefing
  summary     Print the portfolio summary (compact)
  serve       Run the HTTP API (uvicorn)
"""
from __future__ import annotations

import argparse
import logging
import sys

from alpro.core.db import init_db, session
from alpro.core.formatting import fmt_money, fmt_pct


def cmd_init_db(_: argparse.Namespace) -> int:
    init_db()
    print("ok: database schema ready")
    return 0


def cmd_demo(_: argparse.Namespace) -> int:
    init_db()
    from alpro.demo import seed_demo

    with session() as s:
        result = seed_demo(s)
    print(f"ok: demo seeded — {result['instruments']} instruments, {result['transactions']} transactions")
    return 0


def cmd_refresh(_: argparse.Namespace) -> int:
    init_db()
    from alpro.data.bist_demo import run_all

    with session() as s:
        reports = run_all(s)
    for r in reports:
        flag = "LIVE" if r.mode == "live" else r.mode.upper()
        print(f"[{flag:>7}] {r.connector:<10} points={r.points_written:<3} {r.message}")
    return 0


def cmd_briefing(_: argparse.Namespace) -> int:
    init_db()
    from alpro.ai.briefing import build_briefing

    with session() as s:
        b = build_briefing(s)
    print(b.text)
    if b.audit_notes:
        print("\n[denetim notları]", *b.audit_notes, sep="\n- ", file=sys.stderr)
    return 0


def cmd_summary(_: argparse.Namespace) -> int:
    init_db()
    from alpro.ai.tools import get_portfolio_summary

    with session() as s:
        ps = get_portfolio_summary(s)
    print(f"Toplam: {fmt_money(ps['total_value'], ps['base_currency'])}")
    if ps["total_unrealized_pl_pct"] is not None:
        print(
            f"Açık K/Z: {fmt_money(ps['total_unrealized_pl'], ps['base_currency'])} "
            f"({fmt_pct(ps['total_unrealized_pl_pct'])})"
        )
    for p in ps["positions"]:
        mv = fmt_money(p["market_value_base"], ps["base_currency"]) if p["market_value_base"] else "—"
        w = fmt_pct(p["weight_pct"], signed=False) if p["weight_pct"] else "—"
        print(f"  {p['symbol']:<9} {mv:>18}  {w}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("alpro.api.app:app", host=args.host, port=args.port, reload=False)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="alpro", description="AL PRO — Finans İşletim Sistemi (Sprint 1)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    sub.add_parser("demo").set_defaults(func=cmd_demo)
    sub.add_parser("refresh").set_defaults(func=cmd_refresh)
    sub.add_parser("briefing").set_defaults(func=cmd_briefing)
    sub.add_parser("summary").set_defaults(func=cmd_summary)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
