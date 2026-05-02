"""Streamlit web UI for Zincir Bilet (chain-ticket TCDD search).

Wraps the existing search engine and formatter logic. No changes to
those modules — this is purely a frontend.

Run:
    streamlit run app.py
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import streamlit as st

from config import get_tcdd_token
from search_engine import (
    Journey,
    Leg,
    OptimalSplit,
    SearchEngine,
    TrainExploration,
    regular_seats,
)
from stations import StationIndex
from tcdd_client import TCDDAuthError


st.set_page_config(
    page_title="Zincir Bilet",
    page_icon="⛓️",
    layout="wide",
)


# ---------- cached resources --------------------------------------------------


@st.cache_resource(show_spinner=False)
def _station_index() -> StationIndex:
    return StationIndex()


# ---------- helpers ----------------------------------------------------------


TCDD_HOMEPAGE = "https://ebilet.tcddtasimacilik.gov.tr"


def _fmt_price(amount: float, currency: str) -> str:
    sym = "₺" if currency in ("TRY", "") else currency
    return f"{amount:,.0f} {sym}".strip()


def _fmt_duration(td: timedelta) -> str:
    total = max(0, int(td.total_seconds()))
    h, rem = divmod(total, 3600)
    m = rem // 60
    if h and m:
        return f"{h}sa {m}dk"
    if h:
        return f"{h}sa"
    return f"{m}dk"


def _hhmm(dt: datetime | None) -> str:
    return dt.strftime("%H:%M") if dt else "  ?  "


def _cabins_summary(leg: Leg) -> str:
    if not leg.cabins:
        return "Yer bilgisi yok"
    parts: list[str] = []
    for c in leg.cabins:
        suffix = " (engelli)" if c.is_accessibility else ""
        parts.append(f"{c.name}: {c.seats}{suffix}")
    return " · ".join(parts)


def _tcdd_link_button() -> None:
    st.link_button(
        "🔗 TCDD'de aç", TCDD_HOMEPAGE,
        type="secondary", use_container_width=True,
    )


# ---------- token guard -------------------------------------------------------


try:
    get_tcdd_token()
except RuntimeError:
    st.error(
        "**TCDD_TOKEN ayarlanmamış.**\n\n"
        "Token nasıl alınır → [TOKEN_GUIDE.md](TOKEN_GUIDE.md) "
        "(Chrome DevTools yöntemiyle birkaç saniyede alabilirsiniz).\n\n"
        "Token'ı `.env` dosyasına ekleyip uygulamayı yeniden başlatın:\n\n"
        "```\nTCDD_TOKEN=<your_token_here>\n```"
    )
    st.stop()


# ---------- station picker ----------------------------------------------------


index = _station_index()
station_names = sorted(s["name"] for s in index.usable)


def _default_index(name: str, fallback: int) -> int:
    return station_names.index(name) if name in station_names else fallback


# ---------- header -----------------------------------------------------------


st.title("⛓️ Zincir Bilet")
st.caption(
    "Direkt sefer dolu mu? Aynı trende koltuk değiştirerek tamamlayabileceğiniz "
    "zincir biletleri bulur."
)


# ---------- two-column layout ------------------------------------------------


col_form, col_results = st.columns([1, 2.5])


with col_form:
    with st.form("search_form", clear_on_submit=False):
        origin_name = st.selectbox(
            "Nereden",
            options=station_names,
            index=_default_index("ESKİŞEHİR", 0),
        )
        dest_name = st.selectbox(
            "Nereye",
            options=station_names,
            index=_default_index("İSTANBUL(SÖĞÜTLÜÇEŞME)", min(1, len(station_names) - 1)),
        )
        the_date = st.date_input(
            "Tarih",
            value=date.today() + timedelta(days=1),
            min_value=date.today(),
            format="DD-MM-YYYY",
        )
        submitted = st.form_submit_button(
            "Bilet Ara", use_container_width=True, type="primary"
        )


# ---------- rendering --------------------------------------------------------


def _render_leg(leg: Leg, *, link_label: str | None = None) -> None:
    head = f"**{link_label}**" if link_label else f"**{leg.train_number}** — {leg.train_name}"
    st.markdown(head)
    cols = st.columns([2, 1, 1])
    cols[0].markdown(
        f"🕐 **{_hhmm(leg.departure_time)} → {_hhmm(leg.arrival_time)}**  \n"
        f"{leg.departure_station_name} → {leg.arrival_station_name}"
    )
    cols[1].metric("Süre", _fmt_duration(leg.duration))
    cols[2].metric("Fiyat", _fmt_price(leg.price, leg.currency))

    seats = regular_seats(leg)
    if seats > 0:
        st.success(f"💺 {seats} yer · {_cabins_summary(leg)}")
    elif leg.cabins:
        st.warning(f"⚠️ Satılık yer yok — {_cabins_summary(leg)}")
    else:
        st.info("Yer yok")


def _render_direct(journey: Journey, *, idx: int) -> None:
    leg = journey.legs[0]
    head = (
        f"🚄 **{leg.train_number}** · "
        f"{_hhmm(leg.departure_time)} → {_hhmm(leg.arrival_time)} · "
        f"{_fmt_price(leg.price, leg.currency)}"
    )
    with st.expander(head, expanded=False):
        _render_leg(leg)
        _tcdd_link_button()


def _render_same_train_split(exp: TrainExploration) -> None:
    direct = exp.direct
    opt = exp.optimal
    if opt is None or not opt.is_complete:
        return
    n = len(opt.segments)
    head = (
        f"⛓️ **{direct.train_number}** · "
        f"{direct.departure_time.strftime('%H:%M')} → "
        f"{direct.arrival_time.strftime('%H:%M')} · "
        f"{n} halka · "
        f"**{_fmt_price(opt.total_price, direct.currency)}**"
    )
    with st.expander(head, expanded=False):
        st.success(
            f"✅ Zincir tamamlandı — **{n} halka**, "
            f"toplam **{_fmt_price(opt.total_price, direct.currency)}**"
        )
        for i, seg in enumerate(opt.segments, 1):
            leg = seg.leg
            if leg is None:
                continue
            with st.container(border=True):
                _render_leg(leg, link_label=f"{i}. Halka")
                _tcdd_link_button()
        if n >= 2:
            transfer_stops = " / ".join(s.dst_name for s in opt.segments[:-1])
            st.caption(
                f"🔁 {opt.transfer_count} halka değişimi · "
                f"{transfer_stops}'de · aynı tren"
            )
        if exp.has_better_split:
            st.info(
                f"⭐ Zincir biletinde daha çok yer var "
                f"(direkt: {regular_seats(direct)}, zincir: {opt.min_seats})"
            )


# ---------- run --------------------------------------------------------------


def _run_search() -> None:
    origin = index.by_name(origin_name)
    dest = index.by_name(dest_name)
    if origin is None or dest is None:
        st.error("İstasyon bulunamadı.")
        return
    if origin["id"] == dest["id"]:
        st.error("Kalkış ve varış aynı istasyon olamaz.")
        return

    api_departure = f"{the_date.strftime('%d-%m-%Y')} 00:00:00"
    label = f"{origin['name']} → {dest['name']}  {the_date.strftime('%d-%m-%Y')}"

    with st.spinner(f"Zincir biletler aranıyor: {label} ..."):
        engine = SearchEngine()
        try:
            journeys = engine.find_journeys(
                origin["id"], dest["id"], api_departure,
                origin_name=origin["name"], dest_name=dest["name"],
            )
            explorations = engine.explore_train_splits(
                origin["id"], dest["id"], api_departure,
                origin_name=origin["name"], dest_name=dest["name"],
            )
        except TCDDAuthError as exc:
            st.error(
                f"**TCDD token reddedildi:** {exc}\n\n"
                "Yeni bir token alın → [TOKEN_GUIDE.md](TOKEN_GUIDE.md)."
            )
            return
        except Exception as exc:
            st.error(f"Arama sırasında hata oluştu: {exc}")
            return

    directs = sorted(
        (j for j in journeys if len(j.legs) == 1),
        key=lambda j: j.legs[0].departure_time,
    )
    same_train_splits = sorted(
        (
            exp for exp in explorations
            if exp.optimal is not None
            and exp.optimal.is_complete
            and not exp.optimal_equals_direct
        ),
        key=lambda e: e.direct.departure_time,
    )

    st.markdown(f"### {label}")

    if not directs and not same_train_splits:
        if engine.last_direct_train_count == 0:
            st.warning("Bu tarih için sefer bulunamadı.")
        else:
            st.warning("Bu tarih için ne direkt bilet ne de zincir bilet bulundu.")
        return

    if directs:
        st.markdown(f"## ✅ Direkt biletler ({len(directs)})")
        for i, j in enumerate(directs, 1):
            _render_direct(j, idx=i)

    if same_train_splits:
        st.markdown(f"## ⛓️ Zincir biletler ({len(same_train_splits)})")
        st.info(
            "💡 Zincir biletini almak için her halkayı TCDD'de ayrı ayrı "
            "aramanız gerekecek."
        )
        for exp in same_train_splits:
            _render_same_train_split(exp)


with col_results:
    if submitted:
        _run_search()
    else:
        st.info("Soldan istasyon ve tarih seçerek bilet aramaya başlayın.")
