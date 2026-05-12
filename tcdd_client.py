"""HTTP client for the TCDD train-availability API.

The token is read from the TCDD_TOKEN environment variable. See
TOKEN_GUIDE.md for instructions on capturing one via Chrome DevTools.

TCDD currently does not validate the JWT's expiration or signature — any
token lifted from the public web client works. That may change without
notice; if 401/403 starts coming back, capture a fresh token.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from config import (
    DEFAULT_HEADERS,
    DEFAULT_TRAIN_TYPES,
    REQUEST_TIMEOUT,
    SEARCH_ENDPOINT,
    SEARCH_PARAMS,
    get_tcdd_token,
)


@dataclass(frozen=True)
class SearchRoute:
    departure_station_id: int
    departure_station_name: str
    arrival_station_id: int
    arrival_station_name: str
    departure_date: str  # "DD-MM-YYYY HH:MM:SS"


class TCDDAuthError(RuntimeError):
    """Raised when the API rejects our token."""


class TCDDClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        train_types: list[str] | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.token = token or get_tcdd_token()
        self.train_types = (
            list(train_types) if train_types is not None else list(DEFAULT_TRAIN_TYPES)
        )
        self.session = session or requests.Session()
        self.last_status_code: int | None = None

    def _headers(self) -> dict[str, str]:
        # Browser sends bare token; "Bearer " prefix also works. Send bare
        # to match the wire exactly.
        return {**DEFAULT_HEADERS, "Authorization": self.token}

    def search(self, route: SearchRoute, *, passengers: int = 1) -> dict[str, Any]:
        body = {
            "searchRoutes": [
                {
                    "departureStationId": route.departure_station_id,
                    "departureStationName": route.departure_station_name,
                    "arrivalStationId": route.arrival_station_id,
                    "arrivalStationName": route.arrival_station_name,
                    "departureDate": route.departure_date,
                }
            ],
            "passengerTypeCounts": [{"id": 0, "count": passengers}],
            "searchReservation": False,
            "blTrainTypes": self.train_types,
        }
        resp = self.session.post(
            SEARCH_ENDPOINT,
            params=SEARCH_PARAMS,
            headers=self._headers(),
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        self.last_status_code = resp.status_code
        if resp.status_code in (401, 403):
            raise TCDDAuthError(
                f"TCDD rejected the token ({resp.status_code}). "
                "Token has likely expired — fetch a fresh one."
            )
        resp.raise_for_status()
        return resp.json()
