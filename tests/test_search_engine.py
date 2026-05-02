"""Fixture-based tests for SearchEngine depth-walking.

Strategy: capture one real direct response (esk_ist_direct.json) and
derive sub-segment responses by slicing each train's `segments` list.
Tests then construct mock clients that map (src_id, dst_id) → response
and verify the engine reaches the expected depth.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from search_engine import SearchEngine, TrainExploration
from tcdd_client import SearchRoute

FIXTURE = Path(__file__).parent / "fixtures" / "esk_ist_direct.json"


# ---------- helpers ------------------------------------------------------------


def load_direct() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _slice_train(train: dict, src_id: int, dst_id: int, *, zero_seats: bool) -> dict | None:
    """Return a copy of `train` with segments restricted to src→dst, or None."""
    segs = train.get("segments") or []
    sliced: list[dict] = []
    in_window = False
    for seg in segs:
        seg_obj = seg.get("segment") or {}
        dep_id = (seg_obj.get("departureStation") or {}).get("id")
        arr_id = (seg_obj.get("arrivalStation") or {}).get("id")
        if not in_window and dep_id == src_id:
            in_window = True
        if in_window:
            sliced.append(seg)
            if arr_id == dst_id:
                break
    if not sliced:
        return None
    last_arr = ((sliced[-1].get("segment") or {}).get("arrivalStation") or {}).get("id")
    if last_arr != dst_id:
        return None
    out = copy.deepcopy(train)
    out["segments"] = sliced
    out["departureStationId"] = src_id
    out["arrivalStationId"] = dst_id
    out["trainSegments"] = []  # force engine to use segments[] (which we control)
    if zero_seats:
        for c in out.get("cabinClassAvailabilities") or []:
            c["availabilityCount"] = 0
    return out


def slice_response(direct: dict, src_id: int, dst_id: int, *, zero_seats: bool = False) -> dict:
    """Synthesize a (src→dst) sub-segment response from the direct fixture."""
    new_tas: list[dict] = []
    for ta in direct["trainLegs"][0]["trainAvailabilities"]:
        for train in ta.get("trains") or []:
            sliced = _slice_train(train, src_id, dst_id, zero_seats=zero_seats)
            if sliced is None:
                continue
            new_tas.append(
                {
                    "trains": [sliced],
                    "totalTripTime": 0,
                    "minPrice": (sliced.get("minPrice") or {}).get("priceAmount", 0),
                    "connection": False,
                    "dayChanged": False,
                }
            )
    return {
        "trainLegs": [{"trainAvailabilities": new_tas, "resultCount": len(new_tas)}],
        "legCount": 1,
        "roundTripDiscount": 0,
        "maxRegionalTrainsRoundTripDays": 0,
    }


class MockClient:
    """Quack-compatible TCDDClient that serves fixtures by (src, dst)."""

    def __init__(self) -> None:
        self._responses: dict[tuple[int, int], dict] = {}

    def add(self, src_id: int, dst_id: int, response: dict) -> None:
        self._responses[(src_id, dst_id)] = response

    def search(self, route: SearchRoute) -> dict:
        key = (route.departure_station_id, route.arrival_station_id)
        if key in self._responses:
            return self._responses[key]
        return {
            "trainLegs": [{"trainAvailabilities": [], "resultCount": 0}],
            "legCount": 1,
        }


# ---------- fixtures -----------------------------------------------------------


@pytest.fixture(scope="module")
def direct_resp() -> dict:
    return load_direct()


@pytest.fixture
def chain(direct_resp) -> list[tuple[int, str]]:
    """Stop chain of ANKARA EKSPRESİ (the train with 10 segments / 11 stops)."""
    train = next(
        t
        for ta in direct_resp["trainLegs"][0]["trainAvailabilities"]
        for t in ta["trains"]
        if "ANKARA EKSPRES" in (t.get("name") or "")
    )
    out: list[tuple[int, str]] = []
    for seg in train["segments"]:
        s = seg["segment"]
        if not out:
            dep = s["departureStation"]
            out.append((dep["id"], dep["name"]))
        arr = s["arrivalStation"]
        out.append((arr["id"], arr["name"]))
    assert len(out) >= 5, "need at least 5 stops on the chain for depth-4 tests"
    return out


# ---------- tests --------------------------------------------------------------


def test_depth1_direct_with_seats(direct_resp):
    """Direct response has seats → engine returns depth-1 journeys."""
    o, d = 93, 1325
    client = MockClient()
    client.add(o, d, direct_resp)
    eng = SearchEngine(client=client)
    journeys = eng.find_journeys(o, d, "05-05-2026 00:00:00", top_n=5)
    assert journeys, "expected depth-1 journeys"
    assert all(len(j.legs) == 1 for j in journeys)
    assert journeys[0].total_price <= journeys[-1].total_price  # sorted asc


def test_depth2_when_direct_sold_out(direct_resp, chain):
    """Zero direct; populate every (o,s) and (s,d) with seats."""
    o, d = chain[0][0], chain[-1][0]
    client = MockClient()
    client.add(o, d, slice_response(direct_resp, o, d, zero_seats=True))
    for stop_id, _ in chain[1:-1]:
        client.add(o, stop_id, slice_response(direct_resp, o, stop_id))
        client.add(stop_id, d, slice_response(direct_resp, stop_id, d))
    eng = SearchEngine(client=client)
    journeys = eng.find_journeys(o, d, "05-05-2026 00:00:00", top_n=10)
    assert journeys, "expected depth-2 journeys"
    assert all(len(j.legs) == 2 for j in journeys)
    # Same-train preference: both legs should share train_id
    for j in journeys:
        assert j.legs[0].train_id == j.legs[1].train_id


def test_depth3_when_depths_1_2_sold_out(direct_resp, chain):
    """Zero direct + every (o,s) and (s,d). Populate one o→s1→s2→d chain."""
    o, d = chain[0][0], chain[-1][0]
    inner = chain[1:-1]
    s1, s2 = inner[0][0], inner[1][0]

    client = MockClient()
    client.add(o, d, slice_response(direct_resp, o, d, zero_seats=True))
    # Block all 2-segment splits
    for stop_id, _ in inner:
        client.add(o, stop_id, slice_response(direct_resp, o, stop_id, zero_seats=True))
        client.add(stop_id, d, slice_response(direct_resp, stop_id, d, zero_seats=True))
    # Open the 3-segment chain o → s1 → s2 → d
    client.add(o, s1, slice_response(direct_resp, o, s1))
    client.add(s1, s2, slice_response(direct_resp, s1, s2))
    client.add(s2, d, slice_response(direct_resp, s2, d))

    eng = SearchEngine(client=client)
    journeys = eng.find_journeys(o, d, "05-05-2026 00:00:00", top_n=10)
    assert journeys, "expected depth-3 journeys"
    assert all(len(j.legs) == 3 for j in journeys)
    legs = journeys[0].legs
    assert [l.departure_station_id for l in legs] == [o, s1, s2]
    assert [l.arrival_station_id for l in legs] == [s1, s2, d]


def test_depth4_when_depths_1_2_3_sold_out(direct_resp, chain):
    """Pick three consecutive inner stops. Block everything except the
    o→s1→s2→s3→d chain. Engine must reach depth 4."""
    o, d = chain[0][0], chain[-1][0]
    inner = chain[1:-1]
    s1, s2, s3 = inner[0][0], inner[1][0], inner[2][0]

    client = MockClient()
    client.add(o, d, slice_response(direct_resp, o, d, zero_seats=True))
    # Block all (o, s_inner) and (s_inner, d)
    for stop_id, _ in inner:
        client.add(o, stop_id, slice_response(direct_resp, o, stop_id, zero_seats=True))
        client.add(stop_id, d, slice_response(direct_resp, stop_id, d, zero_seats=True))
    # Block all inner pairs (so depth 3 fails for any combo)
    for i, (a, _) in enumerate(inner):
        for b, _ in inner[i + 1 :]:
            client.add(a, b, slice_response(direct_resp, a, b, zero_seats=True))
    # Open the 4-segment chain
    client.add(o, s1, slice_response(direct_resp, o, s1))
    client.add(s1, s2, slice_response(direct_resp, s1, s2))
    client.add(s2, s3, slice_response(direct_resp, s2, s3))
    client.add(s3, d, slice_response(direct_resp, s3, d))

    eng = SearchEngine(client=client, max_depth=4)
    journeys = eng.find_journeys(o, d, "05-05-2026 00:00:00", top_n=10)
    assert journeys, "expected depth-4 journeys"
    assert all(len(j.legs) == 4 for j in journeys)
    legs = journeys[0].legs
    assert [l.departure_station_id for l in legs] == [o, s1, s2, s3]
    assert [l.arrival_station_id for l in legs] == [s1, s2, s3, d]


def test_no_route_returns_empty(direct_resp, chain):
    """Zero everything → engine returns empty list (not error)."""
    o, d = chain[0][0], chain[-1][0]
    client = MockClient()
    client.add(o, d, slice_response(direct_resp, o, d, zero_seats=True))
    for stop_id, _ in chain[1:-1]:
        client.add(o, stop_id, slice_response(direct_resp, o, stop_id, zero_seats=True))
        client.add(stop_id, d, slice_response(direct_resp, stop_id, d, zero_seats=True))
    eng = SearchEngine(client=client)
    journeys = eng.find_journeys(o, d, "05-05-2026 00:00:00", top_n=5)
    assert journeys == []


def test_wheelchair_only_does_not_count_as_seats(direct_resp, chain):
    """Trains with only wheelchair (showAvailabilityOnQuery=true) availability
    must NOT be reported as direct journeys — splits should engage instead."""
    o, d = chain[0][0], chain[-1][0]
    # Direct response: zero out regular cabins, leave accessibility cabins
    direct = slice_response(direct_resp, o, d)
    for ta in direct["trainLegs"][0]["trainAvailabilities"]:
        for train in ta["trains"]:
            for c in train.get("cabinClassAvailabilities") or []:
                cls = c.get("cabinClass") or {}
                if cls.get("showAvailabilityOnQuery"):
                    c["availabilityCount"] = 2  # wheelchair seats present
                else:
                    c["availabilityCount"] = 0  # regular cabins sold out

    client = MockClient()
    client.add(o, d, direct)
    # Sub-segments have full regular availability so depth-2 split can succeed
    for stop_id, _ in chain[1:-1]:
        client.add(o, stop_id, slice_response(direct_resp, o, stop_id))
        client.add(stop_id, d, slice_response(direct_resp, stop_id, d))

    eng = SearchEngine(client=client)
    journeys = eng.find_journeys(o, d, "05-05-2026 00:00:00", top_n=10)
    assert journeys, "expected split journeys"
    # Critical: must NOT have returned the wheelchair-only direct as a result
    assert all(len(j.legs) == 2 for j in journeys), (
        "wheelchair-only direct was incorrectly treated as available"
    )


def test_date_filter_excludes_next_day_rollover(direct_resp, chain):
    """If the API rolls forward into the next day, those trains must be
    filtered out — caller should see empty results, not silent next-day data."""
    o, d = chain[0][0], chain[-1][0]
    # Fixture trains depart on 2026-05-05. Search 2026-05-04 → all should be filtered.
    client = MockClient()
    client.add(o, d, direct_resp)
    eng = SearchEngine(client=client)
    journeys = eng.find_journeys(o, d, "04-05-2026 00:00:00", top_n=10)
    assert journeys == []
    assert eng.last_direct_train_count == 0, (
        "no trains should remain after date filter"
    )


def test_time_hint_prefers_after_hint_trains(direct_resp, chain):
    """Trains departing >= time_hint should sort before earlier trains."""
    from datetime import time as time_obj
    o, d = chain[0][0], chain[-1][0]
    client = MockClient()
    client.add(o, d, direct_resp)
    eng = SearchEngine(client=client)
    # Fixture has trains starting at 01:28 (overnight) up through morning.
    # With hint=06:00, the 06:40+ trains should come before the 01:28 train.
    journeys = eng.find_journeys(
        o, d, "05-05-2026 00:00:00",
        time_hint=time_obj(6, 0), top_n=10,
    )
    assert len(journeys) >= 2
    # First result must depart at or after 06:00
    assert journeys[0].legs[0].departure_time.time() >= time_obj(6, 0)


def test_explore_optimal_is_direct_when_direct_has_seats(direct_resp, chain):
    """Greedy longest-first picks the whole route when direct is sellable —
    optimal == direct, and no per-segment view is computed unless verbose."""
    o, d = chain[0][0], chain[-1][0]
    client = MockClient()
    client.add(o, d, direct_resp)
    eng = SearchEngine(client=client)
    explorations = eng.explore_train_splits(o, d, "05-05-2026 00:00:00")
    assert explorations
    # Only trains with regular direct seats can be checked here — others
    # would need sub-segment fixtures populated to find any split.
    direct_seat_trains = [e for e in explorations if e.direct.has_seats]
    assert direct_seat_trains, "fixture should contain at least one train with direct seats"
    for e in direct_seat_trains:
        assert e.optimal is not None
        assert e.optimal.is_complete
        assert len(e.optimal.segments) == 1
        assert e.optimal_equals_direct
        assert e.split is None  # not verbose


def test_explore_verbose_adds_per_segment_view(direct_resp, chain):
    """verbose=True populates the SplitAnalysis as well as the OptimalSplit."""
    o, d = chain[0][0], chain[-1][0]
    client = MockClient()
    client.add(o, d, direct_resp)
    for (a_id, _), (b_id, _) in zip(chain, chain[1:]):
        client.add(a_id, b_id, slice_response(direct_resp, a_id, b_id))
    eng = SearchEngine(client=client)
    explorations = eng.explore_train_splits(
        o, d, "05-05-2026 00:00:00", verbose=True
    )
    ank = next(
        e for e in explorations if "ANKARA EKSPRES" in e.direct.train_name
    )
    assert ank.split is not None
    assert ank.split.all_sellable
    assert len(ank.split.segments) == len(chain) - 1


def test_greedy_skips_unsellable_intermediate(direct_resp, chain):
    """When a consecutive segment is unsellable but a longer hop bypassing it
    IS sellable, greedy picks the longer hop and finishes the journey."""
    o, d = chain[0][0], chain[-1][0]
    inner = chain[1:-1]
    # Block direct and block (origin → first inner stop). But allow a
    # longer hop (origin → second inner stop) and the remainder.
    s1 = inner[0][0]
    s2 = inner[1][0]
    client = MockClient()
    client.add(o, d, slice_response(direct_resp, o, d, zero_seats=True))
    client.add(o, s1, slice_response(direct_resp, o, s1, zero_seats=True))
    client.add(o, s2, slice_response(direct_resp, o, s2))  # the skip-to target
    # remainder s2 → d
    client.add(s2, d, slice_response(direct_resp, s2, d))
    eng = SearchEngine(client=client)
    explorations = eng.explore_train_splits(o, d, "05-05-2026 00:00:00")
    ank = next(
        e for e in explorations if "ANKARA EKSPRES" in e.direct.train_name
    )
    assert ank.optimal is not None
    assert ank.optimal.is_complete
    # Greedy's first jump was o → s2 (longer than o → s1)
    assert ank.optimal.segments[0].dst_id == s2


def test_explore_train_only_filter(direct_resp, chain):
    """train_number_filter restricts to a single train."""
    o, d = chain[0][0], chain[-1][0]
    client = MockClient()
    client.add(o, d, direct_resp)
    for (a_id, _), (b_id, _) in zip(chain, chain[1:]):
        client.add(a_id, b_id, slice_response(direct_resp, a_id, b_id))

    # Pick the first train's number from the fixture
    target_number = direct_resp["trainLegs"][0]["trainAvailabilities"][0]["trains"][0]["number"]
    eng = SearchEngine(client=client)
    explorations = eng.explore_train_splits(
        o, d, "05-05-2026 00:00:00",
        train_number_filter=target_number,
    )
    assert len(explorations) == 1
    assert explorations[0].direct.train_number == target_number


def test_greedy_bottleneck_when_no_forward_hop(direct_resp, chain):
    """Greedy gets stuck when no downstream target from the current position
    is sellable — bottleneck_station names that current position."""
    o, d = chain[0][0], chain[-1][0]
    inner = chain[1:-1]
    s1 = inner[0][0]
    s1_name = inner[0][1]
    client = MockClient()
    # Direct sold out
    client.add(o, d, slice_response(direct_resp, o, d, zero_seats=True))
    # First sellable hop: o → s1 (everything else from o blocked)
    for stop_id, _ in chain[1:]:
        if stop_id == s1:
            client.add(o, stop_id, slice_response(direct_resp, o, stop_id))
        else:
            client.add(o, stop_id, slice_response(direct_resp, o, stop_id, zero_seats=True))
    # From s1 onward, ALL forward hops blocked
    for stop_id, _ in chain[2:]:
        client.add(s1, stop_id, slice_response(direct_resp, s1, stop_id, zero_seats=True))

    eng = SearchEngine(client=client)
    explorations = eng.explore_train_splits(o, d, "05-05-2026 00:00:00")
    ank = next(
        e for e in explorations if "ANKARA EKSPRES" in e.direct.train_name
    )
    assert ank.optimal is not None
    assert not ank.optimal.is_complete
    # Got the first hop, then stuck at s1
    assert len(ank.optimal.segments) == 1
    assert ank.optimal.segments[0].dst_id == s1
    assert ank.optimal.bottleneck_station == s1_name


def test_has_better_split_only_when_optimal_differs_from_direct(direct_resp, chain):
    """has_better_split fires only when the optimal multi-ticket split has
    strictly more min seats than direct. When direct has seats, greedy picks
    direct as a 1-ticket optimal — no comparison happens."""
    o, d = chain[0][0], chain[-1][0]
    # Direct: 1 regular seat. Block the whole-route long hop AFTER this
    # (impossible since direct = whole route), so we need a different setup.
    # Instead: zero direct entirely, force greedy into multi-ticket. Then
    # populate consecutive sub-segments with high seat counts.
    client = MockClient()
    client.add(o, d, slice_response(direct_resp, o, d, zero_seats=True))
    # Block all long hops from origin except the very first inner stop.
    inner = chain[1:-1]
    s1 = inner[0][0]
    for stop_id, _ in chain[1:]:
        if stop_id == s1:
            client.add(o, stop_id, slice_response(direct_resp, o, stop_id))
        else:
            client.add(o, stop_id, slice_response(direct_resp, o, stop_id, zero_seats=True))
    # From s1, block all skip targets except direct s1 → d
    for stop_id, _ in chain[2:-1]:
        client.add(s1, stop_id, slice_response(direct_resp, s1, stop_id, zero_seats=True))
    client.add(s1, d, slice_response(direct_resp, s1, d))

    eng = SearchEngine(client=client)
    explorations = eng.explore_train_splits(o, d, "05-05-2026 00:00:00")
    multi = [
        e for e in explorations
        if e.optimal is not None and e.optimal.is_complete and len(e.optimal.segments) >= 2
    ]
    assert multi, "expected at least one multi-ticket optimal split"
    # has_better_split is True iff min seats across optimal segments > direct seats.
    # Direct was zeroed, so any positive split min counts as 'better'.
    assert any(e.has_better_split for e in multi)


def test_max_depth_respected(direct_resp, chain):
    """With max_depth=2, engine must NOT return depth-3 results even if available."""
    o, d = chain[0][0], chain[-1][0]
    inner = chain[1:-1]
    s1, s2 = inner[0][0], inner[1][0]

    client = MockClient()
    client.add(o, d, slice_response(direct_resp, o, d, zero_seats=True))
    for stop_id, _ in inner:
        client.add(o, stop_id, slice_response(direct_resp, o, stop_id, zero_seats=True))
        client.add(stop_id, d, slice_response(direct_resp, stop_id, d, zero_seats=True))
    # 3-segment chain available — but max_depth=2 should refuse to use it
    client.add(o, s1, slice_response(direct_resp, o, s1))
    client.add(s1, s2, slice_response(direct_resp, s1, s2))
    client.add(s2, d, slice_response(direct_resp, s2, d))

    eng = SearchEngine(client=client, max_depth=2)
    assert eng.find_journeys(o, d, "05-05-2026 00:00:00") == []
