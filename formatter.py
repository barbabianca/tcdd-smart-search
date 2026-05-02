"""User-facing rendering of Journey objects."""
from __future__ import annotations

from datetime import timedelta

from search_engine import (
    Journey,
    Leg,
    OptimalSplit,
    SegmentAttempt,
    SplitAnalysis,
    TrainExploration,
    regular_seats,
)


def _fmt_duration(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total < 0:
        total = 0
    h, rem = divmod(total, 3600)
    m = rem // 60
    if h and m:
        return f"{h}sa {m}dk"
    if h:
        return f"{h}sa"
    return f"{m}dk"


def _fmt_price(amount: float, currency: str) -> str:
    # Empty currency happens when a train has zero cabin availability and the
    # API returns minPrice without priceCurrency. TCDD only sells in TRY, so
    # default to ₺ rather than rendering a bare number.
    sym = "₺" if currency in ("TRY", "") else currency
    return f"{amount:,.0f} {sym}".strip()


def _fmt_cabins(leg: Leg) -> str:
    if not leg.cabins:
        return "yer yok"
    # Don't call .title() — it breaks Turkish (EKONOMİ -> 'Ekonomi̇').
    parts: list[str] = []
    for c in leg.cabins:
        if c.is_accessibility:
            parts.append(f"{c.name}: {c.seats} (Sadece engelli koltuğu)")
        else:
            parts.append(f"{c.name}: {c.seats}")
    return ", ".join(parts)


def render_leg(leg: Leg, *, index: int | None = None) -> str:
    prefix = f"  [{index}] " if index is not None else "  "
    dep = leg.departure_time.strftime("%d.%m %H:%M")
    arr = leg.arrival_time.strftime("%d.%m %H:%M")
    return (
        f"{prefix}{dep} → {arr}  "
        f"{leg.departure_station_name} → {leg.arrival_station_name}\n"
        f"     Tren: {leg.train_name} ({leg.train_number})  "
        f"Süre: {_fmt_duration(leg.duration)}\n"
        f"     Fiyat: {_fmt_price(leg.price, leg.currency)}  Yer: {_fmt_cabins(leg)}"
    )


def render_journey(journey: Journey, *, header: str = "") -> str:
    if not journey.legs:
        return "Yolculuk yok."
    lines: list[str] = []
    if header:
        lines.append(header)
    n = len(journey.legs)
    if n == 1:
        lines.append("DİREKT")
    else:
        lines.append(f"{n} BACAKLI YOLCULUK ({journey.transfer_count} aktarma)")
    for i, leg in enumerate(journey.legs, 1):
        lines.append(render_leg(leg, index=i if n > 1 else None))
    lines.append("")
    lines.append(
        f"  Toplam: {_fmt_price(journey.total_price, journey.currency)}  "
        f"Süre: {_fmt_duration(journey.total_duration)}  "
        f"Aktarma: {journey.transfer_count}"
    )
    if journey.is_multi_train:
        lines.append("  ⚠ ÇOK TRENLİ AKTARMA")
    for w in journey.warnings:
        lines.append(f"  ⚠ {w}")
    return "\n".join(lines)


def _hhmm(dt) -> str:
    return dt.strftime("%H:%M") if dt else "  ?  "


def _render_segment_line(seg: SegmentAttempt, *, currency: str) -> str:
    times = f"{_hhmm(seg.expected_dep_time)} → {_hhmm(seg.expected_arr_time)}"
    route = f"{seg.src_name} → {seg.dst_name}"
    if seg.is_sellable and seg.leg is not None:
        n = regular_seats(seg.leg)
        return (
            f"    ✓ {times}  {route}: {n} yer "
            f"({_fmt_cabins(seg.leg)})  "
            f"{_fmt_price(seg.leg.price, seg.leg.currency or currency)}"
        )
    return f"    ✗ {times}  {route}: SATILMIYOR"


def _render_direct_line(direct: Leg) -> str:
    if direct.has_seats:
        return (
            f"  Direkt: {regular_seats(direct)} yer "
            f"({_fmt_cabins(direct)})  "
            f"{_fmt_price(direct.price, direct.currency)}"
        )
    if direct.cabins:
        return f"  Direkt: yer yok — {_fmt_cabins(direct)}"
    return "  Direkt: yer yok"


def _render_split_block(split: SplitAnalysis, direct: Leg) -> list[str]:
    lines: list[str] = [
        f"  Segment-segment uygunluk ({len(split.segments)} segment):"
    ]
    currency = direct.currency
    for seg in split.segments:
        lines.append(_render_segment_line(seg, currency=currency))
    if split.all_sellable:
        diff = split.total_price - direct.price
        if abs(diff) < 0.5:
            diff_str = "direkt ile aynı fiyat"
        elif diff > 0:
            diff_str = f"{_fmt_price(diff, currency)} fazla"
        else:
            diff_str = f"{_fmt_price(-diff, currency)} az"
        lines.append(
            f"    Toplam: {_fmt_price(split.total_price, currency)} ({diff_str})"
        )
    return lines


def _render_optimal_block(opt: OptimalSplit, direct: Leg) -> list[str]:
    currency = direct.currency
    lines: list[str] = []
    n = len(opt.segments)
    if not opt.is_complete and n == 0:
        # bottleneck immediately at origin — caller handles via verdict line
        return lines
    header = f"  En iyi parçalı çözüm ({n} bilet):"
    if not opt.is_complete:
        header = f"  Parçalı (eksik — bottleneck: {opt.bottleneck_station}):"
    lines.append("")
    lines.append(header)
    for i, seg in enumerate(opt.segments, 1):
        leg = seg.leg
        if leg is None:
            continue
        seats = regular_seats(leg)
        lines.append(
            f"    Bilet {i}: {_hhmm(seg.expected_dep_time)} {seg.src_name} → "
            f"{_hhmm(seg.expected_arr_time)} {seg.dst_name}"
        )
        lines.append(f"      Yer: {seats} ({_fmt_cabins(leg)})")
        lines.append(f"      Fiyat: {_fmt_price(leg.price, leg.currency or currency)}")
    if opt.is_complete and n >= 1:
        lines.append(f"    Toplam: {_fmt_price(opt.total_price, currency)}")
        if n >= 2:
            transfer_stops = " / ".join(s.dst_name for s in opt.segments[:-1])
            lines.append(
                f"    Aktarma: {opt.transfer_count} koltuk değişikliği "
                f"(aynı tren, durak: {transfer_stops})"
            )
    return lines


def render_exploration(exp: TrainExploration, *, verbose: bool = False) -> str:
    direct = exp.direct
    lines: list[str] = []
    lines.append(
        f"Tren {direct.train_number} "
        f"({direct.departure_time.strftime('%H:%M')} → "
        f"{direct.arrival_time.strftime('%H:%M')})  "
        f"{direct.train_name}"
    )
    lines.append(_render_direct_line(direct))

    if exp.optimal is None:
        # chain too short for splits
        if exp.blocking_reason:
            lines.append(f"  {exp.blocking_reason}")
        return "\n".join(lines)

    # Suppress optimal block when it's just direct (no new info)
    if not exp.optimal_equals_direct:
        lines.extend(_render_optimal_block(exp.optimal, direct))

    if exp.has_better_split:
        d_seats = regular_seats(direct)
        lines.append(
            f"    ⭐ Parçalı seçenekte daha çok yer var "
            f"(direkt: {d_seats}, parçalı min: {exp.optimal.min_seats})"
        )

    if verbose and exp.split is not None:
        lines.append("")
        lines.append("  Detay (segment-segment):")
        for seg in exp.split.segments:
            lines.append(_render_segment_line(seg, currency=direct.currency))

    # Verdict — only when not redundant with direct
    if not exp.optimal_equals_direct:
        if exp.optimal.is_complete:
            lines.append(
                f"  ✓ Tam parçalı satın alma mümkün "
                f"({len(exp.optimal.segments)} bilet, "
                f"toplam {_fmt_price(exp.optimal.total_price, direct.currency)})"
            )
        else:
            lines.append(
                f"  ✗ Bu tren için tam parçalı satın alma mümkün değil "
                f"(bottleneck: {exp.optimal.bottleneck_station})"
            )

    return "\n".join(lines)


def render_explorations(
    explorations: list[TrainExploration],
    *,
    route_label: str = "",
    verbose: bool = False,
) -> str:
    if not explorations:
        return f"Bu tarih için sefer bulunamadı: {route_label}" if route_label else "Sefer yok."
    parts: list[str] = []
    if route_label:
        parts.append(f"=== {route_label} ===")
        parts.append("")
    explorations_sorted = sorted(explorations, key=lambda e: e.direct.departure_time)
    for exp in explorations_sorted:
        parts.append(render_exploration(exp, verbose=verbose))
        parts.append("")
    out = "\n".join(parts).rstrip()
    if not verbose:
        out += "\n\n[Detaylı segment görünümü için --verbose ekleyin]"
    return out


def render_results(journeys: list[Journey], *, route_label: str = "") -> str:
    if not journeys:
        return f"Yer bulunamadı: {route_label}" if route_label else "Yer bulunamadı."
    parts: list[str] = []
    if route_label:
        parts.append(f"=== {route_label} ===")
    for i, j in enumerate(journeys, 1):
        parts.append(render_journey(j, header=f"--- Seçenek {i} ---"))
        parts.append("")
    return "\n".join(parts).rstrip()
