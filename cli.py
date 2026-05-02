"""CLI for TCDD smart search.

Usage:
    python3 cli.py                                              # default smoke test
    python3 cli.py ESKİŞEHİR İSTANBUL 05-05-2026
    python3 cli.py ESKİŞEHİR İSTANBUL 05-05-2026 21:00          # with departure time hint
    python3 cli.py ... --explore-splits                         # show direct + split for each train
    python3 cli.py ... --train-only 81017                       # restrict explore to one train
    python3 cli.py --search ANKARA                              # name lookup helper
    python3 cli.py --raw ESKİŞEHİR İSTANBUL 05-05-2026          # dump raw JSON
"""
from __future__ import annotations

import json
import sys
from datetime import time as time_obj

from formatter import render_explorations, render_results
from search_engine import SearchEngine
from stations import StationIndex
from tcdd_client import SearchRoute, TCDDAuthError, TCDDClient


def _resolve(index: StationIndex, query: str) -> dict:
    hit = index.by_name(query)
    if hit:
        return hit
    matches = index.search(query, limit=10)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"No station matches '{query}'")
    print(f"Ambiguous '{query}'. Candidates:", file=sys.stderr)
    for m in matches:
        print(f"  {m['id']:>5}  {m['name']}", file=sys.stderr)
    raise SystemExit(2)


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--search":
        index = StationIndex()
        for m in index.search(argv[1] if len(argv) > 1 else "", limit=20):
            print(f"{m['id']:>5}  {m['name']}")
        return 0

    raw_mode = False
    if argv and argv[0] == "--raw":
        raw_mode = True
        argv = argv[1:]

    # extract --explore-splits, --verbose, --train-only (anywhere in argv)
    explore = "--explore-splits" in argv
    argv = [a for a in argv if a != "--explore-splits"]
    verbose = "--verbose" in argv
    argv = [a for a in argv if a != "--verbose"]
    train_only: str | None = None
    if "--train-only" in argv:
        i = argv.index("--train-only")
        if i + 1 >= len(argv):
            print("--train-only requires a train number", file=sys.stderr)
            return 2
        train_only = argv[i + 1]
        argv = argv[:i] + argv[i + 2 :]
        explore = True  # --train-only implies explore mode

    index = StationIndex()
    if len(argv) >= 3:
        origin = _resolve(index, argv[0])
        dest = _resolve(index, argv[1])
        date = argv[2]
        user_time: str | None = argv[3] if len(argv) >= 4 else None
    else:
        origin = _resolve(index, "ESKİŞEHİR")
        dest = _resolve(index, "İSTANBUL(SÖĞÜTLÜÇEŞME)")
        date = "05-05-2026"
        user_time = "06:00"

    # Always send 00:00:00 to the API — TCDD rolls forward into the next day
    # if no trains exist after the requested datetime, which silently masks
    # "no trains for this date". Engine filters response by date.
    api_departure = f"{date} 00:00:00"

    hint: time_obj | None = None
    display_time = ""
    if user_time:
        parts = user_time.split(":")
        if len(parts) >= 2:
            try:
                hint = time_obj(int(parts[0]), int(parts[1]))
                display_time = f" {user_time}"
            except ValueError:
                pass

    if raw_mode:
        client = TCDDClient()
        try:
            data = client.search(SearchRoute(
                origin["id"], origin["name"], dest["id"], dest["name"], api_departure
            ))
        except TCDDAuthError as exc:
            print(f"AUTH FAILED: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(data, ensure_ascii=False, indent=2)[:6000])
        return 0

    label = f"{origin['name']} → {dest['name']}  {date}{display_time}"
    print(f"Aranıyor: {label} ...", file=sys.stderr)
    engine = SearchEngine()

    if explore:
        explorations = engine.explore_train_splits(
            origin["id"], dest["id"], api_departure,
            origin_name=origin["name"], dest_name=dest["name"],
            train_number_filter=train_only,
            verbose=verbose,
        )
        if engine.last_direct_train_count == 0:
            print(f"=== {label} ===")
            print("Bu tarih için sefer bulunamadı.")
            return 1
        if not explorations and train_only:
            print(f"=== {label} ===")
            print(f"Bu tarihte {train_only} numaralı tren yok.")
            return 1
        print(render_explorations(explorations, route_label=label, verbose=verbose))
        return 0

    journeys = engine.find_journeys(
        origin["id"], dest["id"], api_departure,
        origin_name=origin["name"], dest_name=dest["name"],
        time_hint=hint,
    )
    if not journeys and engine.last_direct_train_count == 0:
        print(f"=== {label} ===")
        print("Bu tarih için sefer bulunamadı.")
        return 1
    print(render_results(journeys, route_label=label))
    return 0 if journeys else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
