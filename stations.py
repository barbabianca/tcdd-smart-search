"""Station name <-> id lookup, sourced from TCDD's public CDN.

The endpoint /datas/stations.json is unauthenticated (verified). We cache
the response on disk to avoid refetching ~3 MB on every run.
"""
from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path
from typing import Any

import requests

from config import DEFAULT_HEADERS, REQUEST_TIMEOUT

STATIONS_URL = "https://cdn-api-prod-ytp.tcddtasimacilik.gov.tr/datas/stations.json"
STATIONS_PARAMS = {"environment": "dev", "userId": "1"}
CACHE_PATH = Path(__file__).parent / ".cache" / "stations.json"
CACHE_TTL_SEC = 7 * 24 * 3600  # one week


def _fetch() -> list[dict[str, Any]]:
    headers = {k: v for k, v in DEFAULT_HEADERS.items() if k.lower() != "content-type"}
    resp = requests.get(
        STATIONS_URL, params=STATIONS_PARAMS, headers=headers, timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()


def _load_cached() -> list[dict[str, Any]] | None:
    if not CACHE_PATH.exists():
        return None
    if time.time() - CACHE_PATH.stat().st_mtime > CACHE_TTL_SEC:
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(data: list[dict[str, Any]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_stations(*, force_refresh: bool = False) -> list[dict[str, Any]]:
    if not force_refresh:
        cached = _load_cached()
        if cached is not None:
            return cached
    data = _fetch()
    _save_cache(data)
    return data


def _is_searchable(s: dict[str, Any]) -> bool:
    # ticketSaleActive is unreliable (e.g. ESKİŞEHİR=False). Use showOnQuery+active.
    return bool(s.get("showOnQuery")) and bool(s.get("active"))


def _normalize(name: str) -> str:
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    return n.upper().replace("İ", "I").replace("I", "I").strip()


class StationIndex:
    """Searchable index over usable stations."""

    def __init__(self, stations: list[dict[str, Any]] | None = None) -> None:
        raw = stations if stations is not None else load_stations()
        self.usable = [s for s in raw if _is_searchable(s)]
        self._by_id = {s["id"]: s for s in self.usable}
        self._by_name = {s["name"]: s for s in self.usable}
        self._by_norm = {_normalize(s["name"]): s for s in self.usable}

    def by_id(self, station_id: int) -> dict[str, Any] | None:
        return self._by_id.get(station_id)

    def by_name(self, name: str) -> dict[str, Any] | None:
        return self._by_name.get(name) or self._by_norm.get(_normalize(name))

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        q = _normalize(query)
        starts = [s for s in self.usable if _normalize(s["name"]).startswith(q)]
        if len(starts) >= limit:
            return starts[:limit]
        contains = [
            s
            for s in self.usable
            if q in _normalize(s["name"]) and s not in starts
        ]
        return (starts + contains)[:limit]
