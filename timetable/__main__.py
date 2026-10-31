"""CLI:  python -m timetable run [--term 1] [--programs IT,SE] [--require "..."] [--offline]
       python -m timetable agents      # print the agent specification table
       python -m timetable web [--port 8000]   # local web platform (FastAPI + browser UI)
       python -m timetable autopilot [--once]  # 24/7 service without the web UI"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .app import agent_table, run
from .config import Settings


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="timetable", description="Multi-agent university timetable generator")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="generate a timetable")
    r.add_argument("--data", type=Path, help="input workbook (.xlsx)")
    r.add_argument("--term", type=int, help="term inside the academic year: 1, 2 or 3")
    r.add_argument("--programs", help="comma separated programme codes, e.g. IT,SE (default: all)")
    r.add_argument("--require", action="append", default=[], help="requirement in natural language (repeatable)")
    r.add_argument("--require-file", type=Path, help="text file, one requirement per line")
    r.add_argument("--saturday", action="store_true", help="allow Saturday from the start")
    r.add_argument("--offline", action="store_true", help="do not call the LLM (rule-based fallbacks)")
    r.add_argument("--seconds", type=float, help="CP-SAT time limit per chunk")
    r.add_argument("--name", help="run folder name under runs/")
    sub.add_parser("agents", help="print the agent table")
    w = sub.add_parser("web", help="start the local web platform")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--reload", action="store_true", help="auto-reload on code changes (development)")
    a = sub.add_parser("autopilot", help="run the 24/7 service: requests -> re-generation -> publication")
    a.add_argument("--once", action="store_true", help="route until the service is idle, then exit")
    a.add_argument("--tick", type=float, help="seconds between checks (default TT_AUTOPILOT_TICK)")
    args = p.parse_args(argv)

    if args.cmd == "autopilot":
        return _autopilot(args.once, args.tick)

    if args.cmd == "web":
        from .web.server import serve

        print(f"Web platform: http://{args.host}:{args.port}  (Ctrl+C to stop)")
        serve(args.host, args.port, args.reload)
        return 0

    if args.cmd == "agents":
        for row in agent_table():
            print(json.dumps(row, ensure_ascii=False, indent=2))
        return 0

    st = Settings()
    kw = {}
    if args.data:
        kw["data_path"] = args.data
    if args.term:
        kw["term"] = args.term
    if args.programs:
        kw["programs"] = tuple(x.strip() for x in args.programs.split(",") if x.strip())
    if args.saturday:
        kw["allow_saturday"] = True
    if args.offline:
        kw["llm_provider"] = "offline"
    if args.seconds:
        kw["solver_time_limit"] = args.seconds
    st = st.with_(**kw)
    reqs = list(args.require)
    if args.require_file:
        reqs += [ln for ln in args.require_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    result = run(st, reqs, args.name)
    print("\n" + json.dumps({k: v for k, v in result.items() if k != "load"}, ensure_ascii=False, indent=2))
    print("agent load:", {a: f"{v['share']:.0%}" for a, v in result["load"]["agents"].items()})
    return 0 if result["status"] == "ok" else 1


def _autopilot(once: bool, tick: float | None) -> int:
    import time

    from .service.autopilot import Autopilot
    from .web.runs import RunManager

    st = Settings().with_(autopilot_tick=tick) if tick else Settings()
    ap = Autopilot(RunManager(), st)
    if once:
        while True:
            r = ap.tick()
            print(json.dumps({k: v for k, v in r.items() if k != "ts"}, ensure_ascii=False), flush=True)
            if r["state"] in ("idle", "error", "deferred"):
                return 0 if r["state"] == "idle" else 1
            time.sleep(2 if r["state"] == "busy" else 0)
    print(f"Autopilot: checks every {ap.tick_s:.0f} s, log in {ap.root} (Ctrl+C to stop)", flush=True)
    ap.start()
    try:
        while ap.running:
            time.sleep(1)
    except KeyboardInterrupt:
        ap.stop(persist=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
