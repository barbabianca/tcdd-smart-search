"""Split-segment search engine.

Tries direct A→C first; if no train has seats, splits the journey at
intermediate stops of candidate trains and assembles A→B₁→…→C from
sub-searches. Same-train splits are preferred; multi-train fallbacks are
allowed but flagged in `Journey.warnings`.

Greedy depth-by-depth: stops at the first depth (1..max_depth) that yields
at least one viable journey.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from tcdd_client import SearchRoute, TCDDClient

# TCDD API timestamps are in Turkey local time (UTC+3 / UTC+4 DST).
# Always parse and display in this zone so results are correct regardless
# of where the server runs (Streamlit Cloud is UTC).
TR_TZ = ZoneInfo("Europe/Istanbul")

MAX_DEPTH = 4
SAME_TRAIN_GAP_MAX = timedelta(minutes=10)
TIGHT_TRANSFER_MIN = timedelta(minutes=2)


@dataclass(frozen=True)
class CabinAvailability:
    code: str
    name: str
    seats: int
    # Accessibility / special-purpose classes (e.g. wheelchair) — TCDD flags
    # these via cabinClass.showAvailabilityOnQuery=true. They are NOT bookable
    # by general passengers and must not count toward seat availability.
    is_accessibility: bool = False


@dataclass(frozen=True)
class Leg:
    """One A-to-B hop on one specific train."""

    train_id: int
    train_name: str
    train_number: str
    train_type: str
    departure_station_id: int
    departure_station_name: str
    arrival_station_id: int
    arrival_station_name: str
    departure_time: datetime
    arrival_time: datetime
    cabins: tuple[CabinAvailability, ...]
    price: float
    currency: str

    @property
    def has_seats(self) -> bool:
        # Only regular cabins count. Wheelchair / accessibility seats look
        # available in the API but are not bookable for general passengers
        # — counting them masks sold-out trains and breaks split fallback.
        return any(c.seats > 0 and not c.is_accessibility for c in self.cabins)

    @property
    def duration(self) -> timedelta:
        return self.arrival_time - self.departure_time


@dataclass
class Journey:
    legs: list[Leg]
    warnings: list[str] = field(default_factory=list)



    @property
    def total_price(self) -> float:
        return sum(l.price for l in self.legs)

    @property
    def currency(self) -> str:
        return self.legs[0].currency if self.legs else ""

    @property
    def total_duration(self) -> timedelta:
        return self.legs[-1].arrival_time - self.legs[0].departure_time

    @property
    def transfer_count(self) -> int:
        return max(0, len(self.legs) - 1)

    @property
    def is_multi_train(self) -> bool:
        return len({l.train_id for l in self.legs}) > 1


def regular_seats(leg: Leg) -> int:
    return sum(c.seats for c in leg.cabins if not c.is_accessibility)


@dataclass(frozen=True)
class SegmentAttempt:
    """One hop on a candidate train: physical timing + sellability."""

    src_id: int
    src_name: str
    dst_id: int
    dst_name: str
    # The train physically traverses these stops at these times regardless of
    # whether TCDD sells the hop as an individual ticket. Derived from the
    # train's own segments[] array.
    expected_dep_time: datetime | None
    expected_arr_time: datetime | None
    # Leg is None when the API returned no train T (target train) availability
    # for this (src, dst) pair — i.e. TCDD doesn't sell this segment on this train.
    leg: Leg | None

    @property
    def is_sellable(self) -> bool:
        return self.leg is not None


@dataclass
class OptimalSplit:
    """Greedy longest-hop same-train split — minimize tickets / seat changes.

    From each position, tries the furthest downstream stop first; the first
    sellable target wins. This handles two real TCDD constraints:
      - some stations are arrival-only on certain trains (suburban Istanbul):
        İZMİT→GEBZE may fail while İZMİT→SÖĞÜTLÜÇEŞME succeeds.
      - users prefer few tickets over many: ESK→İZMİT in one ticket beats
        five consecutive sub-hops.
    """

    segments: list[SegmentAttempt]  # all sellable; longest-first jumps
    bottleneck_station: str | None  # set when no forward hop is sellable

    @property
    def is_complete(self) -> bool:
        return self.bottleneck_station is None and bool(self.segments)

    @property
    def total_price(self) -> float:
        return sum(s.leg.price for s in self.segments if s.leg is not None)

    @property
    def transfer_count(self) -> int:
        return max(0, len(self.segments) - 1)

    @property
    def min_seats(self) -> int:
        if not self.is_complete:
            return 0
        return min(regular_seats(s.leg) for s in self.segments if s.leg is not None)


@dataclass
class SplitAnalysis:
    """Per-segment breakdown of a single train's full split.

    We only ever report same-train availability. Cross-train fallbacks were
    intentionally removed — a different train at an inconvenient hour at the
    transfer station is operationally useless and creates false confidence.
    """

    segments: list[SegmentAttempt]

    @property
    def all_sellable(self) -> bool:
        return bool(self.segments) and all(s.is_sellable for s in self.segments)

    @property
    def successful_legs(self) -> list[Leg]:
        return [s.leg for s in self.segments if s.leg is not None]

    @property
    def total_price(self) -> float:
        return sum(l.price for l in self.successful_legs)

    @property
    def min_seats(self) -> int:
        if not self.all_sellable:
            return 0
        return min(regular_seats(l) for l in self.successful_legs)

    @property
    def bottleneck_station(self) -> str | None:
        """Name of the source station of the first non-sellable segment.

        i.e. the station where same-train booking can't continue past.
        Returns None when every segment is sellable.
        """
        for seg in self.segments:
            if not seg.is_sellable:
                return seg.src_name
        return None


@dataclass
class TrainExploration:
    """Single-train direct + split analysis.

    `optimal` is the primary output: minimal-ticket greedy split.
    `split` (consecutive-stop ✓/✗ table) is computed only when verbose.
    """

    direct: Leg
    optimal: OptimalSplit | None  # None only when chain has < 3 stops
    split: SplitAnalysis | None = None  # populated only with verbose=True
    blocking_reason: str | None = None  # set when optimal is None

    @property
    def optimal_equals_direct(self) -> bool:
        if self.optimal is None or len(self.optimal.segments) != 1:
            return False
        s = self.optimal.segments[0]
        return (
            s.src_id == self.direct.departure_station_id
            and s.dst_id == self.direct.arrival_station_id
        )

    @property
    def has_better_split(self) -> bool:
        if self.optimal is None or not self.optimal.is_complete:
            return False
        if self.optimal_equals_direct:
            return False
        direct_seats = regular_seats(self.direct)
        return self.optimal.min_seats > direct_seats


# ---------- response parsing ---------------------------------------------------


def _ts_ms_to_dt(ms: int | None) -> datetime | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=TR_TZ)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TR_TZ)
        return dt.astimezone(TR_TZ)
    except ValueError:
        return None


def _user_leg_window(train: dict, origin_id: int, dest_id: int) -> tuple[datetime, datetime] | None:
    """Find the time window for the (origin_id → dest_id) sub-route on this train.

    `train.segments` is the user's leg expressed as consecutive hops. We pick
    the first hop where departureStation == origin and the last where
    arrivalStation == dest.
    """
    segs = train.get("segments") or []
    dep_dt = arr_dt = None
    for seg in segs:
        s_obj = seg.get("segment") or {}
        dep_st = (s_obj.get("departureStation") or {}).get("id")
        arr_st = (s_obj.get("arrivalStation") or {}).get("id")
        if dep_dt is None and dep_st == origin_id:
            dep_dt = _ts_ms_to_dt(seg.get("departureTime"))
        if arr_st == dest_id:
            arr_dt = _ts_ms_to_dt(seg.get("arrivalTime"))
    if dep_dt and arr_dt:
        return dep_dt, arr_dt
    # fallback: trainSegments has flat ISO strings
    for ts in train.get("trainSegments") or []:
        if ts.get("departureStationId") == origin_id and dep_dt is None:
            dep_dt = _parse_iso(ts.get("departureTime"))
        if ts.get("arrivalStationId") == dest_id:
            arr_dt = _parse_iso(ts.get("arrivalTime"))
    if dep_dt and arr_dt:
        return dep_dt, arr_dt
    return None


def _stops_chain(train: dict, origin_id: int, dest_id: int) -> list[tuple[int, str]]:
    """Ordered (station_id, station_name) chain for the user's leg portion."""
    chain: list[tuple[int, str]] = []
    for seg in train.get("segments") or []:
        s_obj = seg.get("segment") or {}
        dep_st = s_obj.get("departureStation") or {}
        arr_st = s_obj.get("arrivalStation") or {}
        if not chain:
            chain.append((dep_st.get("id"), dep_st.get("name") or ""))
        chain.append((arr_st.get("id"), arr_st.get("name") or ""))
    if chain and chain[0][0] == origin_id and any(s[0] == dest_id for s in chain):
        # truncate at dest if the segments list extends past it (shouldn't, but safe)
        end = next(i for i, s in enumerate(chain) if s[0] == dest_id)
        return chain[: end + 1]
    return []


def _parse_response(
    response: dict, origin_id: int, dest_id: int
) -> list[tuple[Leg, dict]]:
    """Return [(Leg, raw_train_dict), ...] for trains in this response."""
    out: list[tuple[Leg, dict]] = []
    for ta in (response.get("trainLegs") or [{}])[0].get("trainAvailabilities") or []:
        for train in ta.get("trains") or []:
            window = _user_leg_window(train, origin_id, dest_id)
            if not window:
                continue
            dep_dt, arr_dt = window
            cabins = tuple(
                CabinAvailability(
                    code=(c.get("cabinClass") or {}).get("code") or "",
                    name=(c.get("cabinClass") or {}).get("name") or "",
                    seats=int(c.get("availabilityCount") or 0),
                    is_accessibility=bool(
                        (c.get("cabinClass") or {}).get("showAvailabilityOnQuery")
                    ),
                )
                for c in train.get("cabinClassAvailabilities") or []
                if int(c.get("availabilityCount") or 0) > 0
            )
            min_price = train.get("minPrice") or {}
            origin_name = ""
            dest_name = ""
            for stop_id, stop_name in _stops_chain(train, origin_id, dest_id):
                if stop_id == origin_id and not origin_name:
                    origin_name = stop_name
                if stop_id == dest_id:
                    dest_name = stop_name
            leg = Leg(
                train_id=int(train.get("id") or 0),
                train_name=str(train.get("name") or ""),
                train_number=str(train.get("number") or ""),
                train_type=str(train.get("type") or ""),
                departure_station_id=origin_id,
                departure_station_name=origin_name,
                arrival_station_id=dest_id,
                arrival_station_name=dest_name,
                departure_time=dep_dt,
                arrival_time=arr_dt,
                cabins=cabins,
                price=float(min_price.get("priceAmount") or 0.0),
                currency=str(min_price.get("priceCurrency") or ""),
            )
            out.append((leg, train))
    return out


# ---------- engine -------------------------------------------------------------


def _date_from_request_string(s: str) -> date | None:
    """Parse 'DD-MM-YYYY [HH:MM:SS]' into a date object."""
    try:
        day_part = s.split(" ", 1)[0]
        d, m, y = day_part.split("-")
        return date(int(y), int(m), int(d))
    except (ValueError, IndexError):
        return None


class SearchEngine:
    def __init__(self, client: TCDDClient | None = None, *, max_depth: int = MAX_DEPTH) -> None:
        self.client = client or TCDDClient()
        self.max_depth = max_depth
        self._cache: dict[tuple[int, int, str], list[tuple[Leg, dict]]] = {}
        # Set after each find_journeys() call. cli.py uses this to differentiate
        # "no trains exist for this date" from "trains exist but no viable seats".
        self.last_direct_train_count: int = 0

    def _query(
        self,
        origin_id: int,
        dest_id: int,
        date_str: str,
        *,
        names: tuple[str, str] = ("", ""),
    ) -> list[tuple[Leg, dict]]:
        key = (origin_id, dest_id, date_str)
        if key in self._cache:
            return self._cache[key]
        route = SearchRoute(
            departure_station_id=origin_id,
            departure_station_name=names[0],
            arrival_station_id=dest_id,
            arrival_station_name=names[1],
            departure_date=date_str,
        )
        try:
            resp = self.client.search(route)
        except Exception:
            self._cache[key] = []
            return []
        parsed = _parse_response(resp, origin_id, dest_id)
        # Strict date filter: TCDD rolls forward into next day if no later
        # trains exist on the requested date. Strip those out so callers can
        # tell the user "no trains for this date" instead of silently showing
        # next-day results.
        requested = _date_from_request_string(date_str)
        if requested is not None:
            parsed = [
                (leg, raw) for leg, raw in parsed
                if leg.departure_time.date() == requested
            ]
        self._cache[key] = parsed
        return parsed

    def find_journeys(
        self,
        origin_id: int,
        dest_id: int,
        date_str: str,
        *,
        origin_name: str = "",
        dest_name: str = "",
        time_hint: time | None = None,
        top_n: int = 5,
    ) -> list[Journey]:
        # Depth 1: direct
        direct = self._query(origin_id, dest_id, date_str, names=(origin_name, dest_name))
        self.last_direct_train_count = len(direct)
        with_seats = [(leg, raw) for leg, raw in direct if leg.has_seats]
        if with_seats:
            journeys = [self._make_journey([leg]) for leg, _ in with_seats]
            return _sort_journeys(journeys, time_hint=time_hint)[:top_n]

        if not direct:
            return []  # no train physically traverses A→C — nothing to split

        # Depth 2..max_depth: try splits on each candidate train's stop chain
        for depth in range(2, self.max_depth + 1):
            results: list[Journey] = []
            seen_paths: set[tuple] = set()
            for cand_leg, cand_raw in direct:
                chain = _stops_chain(cand_raw, origin_id, dest_id)
                if len(chain) < depth + 1:
                    continue  # not enough stops to support this split depth
                inner = chain[1:-1]
                for combo in itertools.combinations(inner, depth - 1):
                    path = [chain[0]] + list(combo) + [chain[-1]]
                    path_key = tuple(s[0] for s in path) + (cand_leg.train_id,)
                    if path_key in seen_paths:
                        continue
                    seen_paths.add(path_key)
                    journey = self._try_path(path, date_str, prefer_train_id=cand_leg.train_id)
                    if journey:
                        results.append(journey)
            if results:
                return _sort_journeys(results, time_hint=time_hint)[:top_n]

        return []

    def explore_train_splits(
        self,
        origin_id: int,
        dest_id: int,
        date_str: str,
        *,
        origin_name: str = "",
        dest_name: str = "",
        train_number_filter: str | None = None,
        verbose: bool = False,
    ) -> list[TrainExploration]:
        """For each direct train, compute:
          - `optimal`: greedy longest-hop same-train split (always)
          - `split`: per-segment consecutive ✓/✗ view (only when verbose=True)
        """
        direct = self._query(origin_id, dest_id, date_str, names=(origin_name, dest_name))
        self.last_direct_train_count = len(direct)
        out: list[TrainExploration] = []
        for direct_leg, raw in direct:
            if train_number_filter and direct_leg.train_number != train_number_filter:
                continue
            chain = _stops_chain(raw, origin_id, dest_id)
            if len(chain) < 3:
                out.append(
                    TrainExploration(
                        direct=direct_leg, optimal=None, split=None,
                        blocking_reason="ara durak yok — direkt = parçalı",
                    )
                )
                continue
            train_segs = raw.get("segments") or []
            optimal = self._greedy_longest_split(
                chain, date_str, train_id=direct_leg.train_id
            )
            split = None
            if verbose:
                split = self._analyze_full_split(
                    train_segs, chain, date_str, train_id=direct_leg.train_id
                )
            out.append(
                TrainExploration(direct=direct_leg, optimal=optimal, split=split)
            )
        return out

    def _greedy_longest_split(
        self,
        chain: list[tuple[int, str]],
        date_str: str,
        *,
        train_id: int,
    ) -> OptimalSplit:
        """From each position, jump to the furthest sellable downstream stop.

        Bypasses arrival-only restrictions on intermediate stations (e.g. when
        İZMİT→GEBZE is unsellable but İZMİT→SÖĞÜTLÜÇEŞME is sellable, we skip
        GEBZE/PENDİK/BOSTANCI by booking the long hop directly).
        """
        n = len(chain)
        segments: list[SegmentAttempt] = []
        current_idx = 0
        while current_idx < n - 1:
            src_id, src_name = chain[current_idx]
            chosen_leg: Leg | None = None
            chosen_target_idx: int | None = None
            # Try furthest target first
            for target_idx in range(n - 1, current_idx, -1):
                dst_id, dst_name = chain[target_idx]
                options = self._query(
                    src_id, dst_id, date_str, names=(src_name, dst_name)
                )
                same = [
                    leg for leg, _ in options
                    if leg.train_id == train_id and leg.has_seats
                ]
                if same:
                    chosen_leg = same[0]
                    chosen_target_idx = target_idx
                    break
            if chosen_leg is None or chosen_target_idx is None:
                # No sellable forward hop — bottleneck at current position.
                return OptimalSplit(segments=segments, bottleneck_station=src_name)
            dst_id, dst_name = chain[chosen_target_idx]
            segments.append(
                SegmentAttempt(
                    src_id=src_id, src_name=src_name,
                    dst_id=dst_id, dst_name=dst_name,
                    expected_dep_time=chosen_leg.departure_time,
                    expected_arr_time=chosen_leg.arrival_time,
                    leg=chosen_leg,
                )
            )
            current_idx = chosen_target_idx
        return OptimalSplit(segments=segments, bottleneck_station=None)

    def _analyze_full_split(
        self,
        train_segments_raw: list[dict],
        chain: list[tuple[int, str]],
        date_str: str,
        *,
        train_id: int,
    ) -> SplitAnalysis:
        segments: list[SegmentAttempt] = []
        for i, ((src_id, src_name), (dst_id, dst_name)) in enumerate(zip(chain, chain[1:])):
            ts_raw = train_segments_raw[i] if i < len(train_segments_raw) else {}
            expected_dep = _ts_ms_to_dt(ts_raw.get("departureTime"))
            expected_arr = _ts_ms_to_dt(ts_raw.get("arrivalTime"))
            options = self._query(src_id, dst_id, date_str, names=(src_name, dst_name))
            same = [
                leg for leg, _ in options
                if leg.train_id == train_id and leg.has_seats
            ]
            leg = same[0] if same else None
            segments.append(
                SegmentAttempt(
                    src_id=src_id, src_name=src_name,
                    dst_id=dst_id, dst_name=dst_name,
                    expected_dep_time=expected_dep,
                    expected_arr_time=expected_arr,
                    leg=leg,
                )
            )
        return SplitAnalysis(segments=segments)

    def _try_path(
        self,
        path: list[tuple[int, str]],
        date_str: str,
        *,
        prefer_train_id: int,
    ) -> Journey | None:
        legs: list[Leg] = []
        for (src_id, src_name), (dst_id, dst_name) in zip(path, path[1:]):
            options = self._query(src_id, dst_id, date_str, names=(src_name, dst_name))
            if not options:
                return None
            same_train = [
                leg for leg, _ in options
                if leg.train_id == prefer_train_id and leg.has_seats
            ]
            if same_train:
                legs.append(same_train[0])
                continue
            any_seats = [leg for leg, _ in options if leg.has_seats]
            if not any_seats:
                return None
            legs.append(min(any_seats, key=lambda l: l.price))
        return self._make_journey(legs)

    def _make_journey(self, legs: list[Leg]) -> Journey:
        warnings: list[str] = []
        for prev, nxt in zip(legs, legs[1:]):
            gap = nxt.departure_time - prev.arrival_time
            station = prev.arrival_station_name or str(prev.arrival_station_id)
            mins = int(gap.total_seconds() // 60)
            if prev.train_id != nxt.train_id:
                warnings.append(
                    f"AKTARMA: farklı tren — {station} ({mins} dk bekleme, peronda transfer gerekiyor)"
                )
            elif gap < TIGHT_TRANSFER_MIN:
                warnings.append(
                    f"DAR AKTARMA: {station} duruşu sadece {mins} dk — gecikme riski"
                )
            elif gap > SAME_TRAIN_GAP_MAX:
                warnings.append(
                    f"UZUN BEKLEME: {station} duruşu {mins} dk (aynı tren ama olağan dışı)"
                )
        return Journey(legs=legs, warnings=warnings)


def _sort_journeys(
    journeys: Iterable[Journey], *, time_hint: time | None = None
) -> list[Journey]:
    """Sort: trains departing >= time_hint first (when given), then cheapest, then fewer legs."""
    def key(j: Journey) -> tuple:
        first_dep = j.legs[0].departure_time
        before_hint = 0
        if time_hint is not None:
            hint_dt = datetime.combine(first_dep.date(), time_hint).replace(tzinfo=first_dep.tzinfo)
            before_hint = 1 if first_dep < hint_dt else 0
        return (before_hint, j.total_price, len(j.legs))

    return sorted(journeys, key=key)
