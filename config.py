"""Static configuration for TCDD smart search."""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()  # loads .env from the current working directory

import os

API_BASE = "https://web-api-prod-ytp.tcddtasimacilik.gov.tr"
SEARCH_ENDPOINT = f"{API_BASE}/tms/train/train-availability"
SEARCH_PARAMS = {"environment": "dev", "userId": "1"}

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr",
    "Content-Type": "application/json",
    "Origin": "https://ebilet.tcddtasimacilik.gov.tr",
    "Referer": "https://ebilet.tcddtasimacilik.gov.tr/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "unit-id": "3895",
}


def get_tcdd_token() -> str:
    """Read TCDD_TOKEN from st.secrets (Streamlit Cloud) or os.environ (.env / local)."""
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "TCDD_TOKEN" in st.secrets:
            return st.secrets["TCDD_TOKEN"]
    except ImportError:
        pass

    token = os.environ.get("TCDD_TOKEN")
    if token:
        return token

    raise RuntimeError(
        "TCDD_TOKEN not set. See TOKEN_GUIDE.md."
    )


# blTrainTypes — what the browser sends by default. Empty array caused 403,
# so leave this populated. Acts like a blacklist (Ankara Ekspresi etc. still
# come through with TURISTIK_TREN listed).
DEFAULT_TRAIN_TYPES: list[str] = ["TURISTIK_TREN"]

REQUEST_TIMEOUT = 20  # seconds
