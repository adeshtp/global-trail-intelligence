"""
Type-ahead suggestions for the location box.

Suggestions come from Photon, which is built for search-as-you-type (the public
Nominatim service forbids it). Picking one is looked up on Nominatim by OSM id,
so the result has exactly the shape a typed search produces and the clicked
place is the place searched.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.routes import search
from app.services import rate_limit
from test_provider_breaker import _client_factory, _down


class _Response:
    def __init__(self, status: int, payload=None) -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                str(self.status_code),
                request=httpx.Request("GET", "x"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


def _photon(*features: dict) -> dict:
    return {"type": "FeatureCollection", "features": list(features)}


def _feature(name: str, osm_type: str = "N", osm_id: int = 1, **props) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [77.06, 10.09]},
        "properties": {
            "name": name,
            "osm_type": osm_type,
            "osm_id": osm_id,
            "osm_key": "place",
            "osm_value": "town",
            **props,
        },
    }


NOMINATIM_ITEM = {
    "lat": "10.0869959",
    "lon": "77.0600915",
    "display_name": "Munnar, Devikulam, Idukki, Kerala, India",
    "name": "Munnar",
    "osm_type": "node",
    "osm_id": 3870703092,
    "category": "place",
    "type": "town",
    "addresstype": "town",
    "boundingbox": ["10.04", "10.12", "77.02", "77.10"],
    "address": {"town": "Munnar"},
}


class PhotonParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        search.photon_breaker.reset()
        search._SUGGEST_CACHE.clear()

    def _suggest(self, payload: dict, query: str = "munn") -> list:
        calls: list[int] = []
        with patch.object(
            search.httpx,
            "AsyncClient",
            _client_factory(lambda: _Response(200, payload), calls),
        ):
            return asyncio.run(search._suggest_uncached(query))

    def test_a_feature_becomes_a_suggestion(self) -> None:
        (suggestion,) = self._suggest(
            _photon(
                _feature(
                    "Munnar",
                    osm_type="R",
                    osm_id=42,
                    state="Kerala",
                    country="India",
                )
            )
        )
        self.assertEqual(suggestion.label, "Munnar")
        self.assertEqual(suggestion.detail, "Kerala, India")
        self.assertEqual(suggestion.osm_type, "relation")
        self.assertEqual(suggestion.osm_id, 42)
        self.assertEqual((suggestion.latitude, suggestion.longitude), (10.09, 77.06))

    def test_the_same_place_is_listed_once(self) -> None:
        rows = self._suggest(
            _photon(_feature("Himalayas", "W", 7), _feature("Himalayas", "W", 7))
        )
        self.assertEqual(len(rows), 1)

    def test_unusable_features_are_skipped(self) -> None:
        bad_geometry = _feature("Nowhere")
        bad_geometry["geometry"] = {"type": "Point", "coordinates": [500, 500]}
        no_name = _feature("")
        unknown_type = _feature("Odd", osm_type="X")
        rows = self._suggest(
            _photon(bad_geometry, no_name, unknown_type, _feature("Munnar", "N", 3))
        )
        self.assertEqual([row.label for row in rows], ["Munnar"])


class PhotonBreakerTests(unittest.TestCase):
    def setUp(self) -> None:
        search.photon_breaker.reset()
        search._SUGGEST_CACHE.clear()

    def tearDown(self) -> None:
        search.photon_breaker.reset()

    def test_an_outage_stops_costing_a_request_per_keystroke(self) -> None:
        calls: list[int] = []
        with patch.object(
            search.httpx, "AsyncClient", _client_factory(_down, calls)
        ), patch.object(search.asyncio, "sleep", new=AsyncMock()):
            for _ in range(8):
                with self.assertRaises(RuntimeError):
                    asyncio.run(search._suggest_uncached("munnar"))
        self.assertEqual(len(calls), 3)

    def test_a_refusal_is_not_retried(self) -> None:
        calls: list[int] = []

        def refuse():
            raise httpx.HTTPStatusError(
                "429", request=httpx.Request("GET", "x"),
                response=httpx.Response(429),
            )

        with patch.object(
            search.httpx, "AsyncClient", _client_factory(refuse, calls)
        ), patch.object(search.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaises(RuntimeError):
                asyncio.run(search._suggest_uncached("munnar"))
        self.assertEqual(len(calls), 1)


class SuggestEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        search._SUGGEST_CACHE.clear()
        search._SEARCH_CACHE.clear()
        self.client = TestClient(app)

    def test_a_short_query_is_rejected(self) -> None:
        self.assertEqual(self.client.get("/api/search/suggest?q=mu").status_code, 422)

    def test_typing_does_not_use_up_the_search_budget(self) -> None:
        self.assertIsNot(search.suggest_limiter, rate_limit.search_limiter)
        with patch.object(
            search, "_suggest_uncached", new=AsyncMock(return_value=[])
        ), patch.object(
            search, "_search_uncached",
            new=AsyncMock(return_value={"query": "x", "results": []}),
        ):
            for index in range(70):
                response = self.client.get(f"/api/search/suggest?q=munnar{index}")
                self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.get("/api/search?q=munnar").status_code, 200)

    def test_an_outage_is_a_502_not_a_crash(self) -> None:
        with patch.object(
            search, "_suggest_uncached",
            new=AsyncMock(side_effect=RuntimeError("down")),
        ):
            self.assertEqual(
                self.client.get("/api/search/suggest?q=munnar").status_code, 502
            )


class LookupTests(unittest.TestCase):
    def setUp(self) -> None:
        search.nominatim_breaker.reset()
        search._SEARCH_CACHE.clear()
        self.client = TestClient(app)

    def _lookup(self, params: str):
        calls: list[int] = []
        with patch.object(
            search.httpx,
            "AsyncClient",
            _client_factory(lambda: _Response(200, [NOMINATIM_ITEM]), calls),
        ):
            return self.client.get(f"/api/search/lookup?{params}")

    def test_it_returns_what_a_typed_search_returns(self) -> None:
        response = self._lookup("osm_type=node&osm_id=3870703092")
        self.assertEqual(response.status_code, 200)
        looked_up = response.json()["results"][0]
        typed = search._normalise_results([NOMINATIM_ITEM])[0]
        self.assertEqual(looked_up, typed)
        for key in ("class", "type", "addresstype", "boundingbox"):
            self.assertIn(key, looked_up)

    def test_it_asks_nominatim_for_that_id(self) -> None:
        captured: list[dict] = []

        class _Client:
            def __init__(self, *a, **k) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc) -> None:
                return None

            async def get(self, url, params=None):
                captured.append({"url": url, **(params or {})})
                return _Response(200, [NOMINATIM_ITEM])

        with patch.object(search.httpx, "AsyncClient", _Client):
            self.client.get("/api/search/lookup?osm_type=relation&osm_id=42")
        self.assertTrue(captured[0]["url"].endswith("/lookup"))
        self.assertEqual(captured[0]["osm_ids"], "R42")

    def test_an_unknown_type_is_rejected(self) -> None:
        self.assertEqual(
            self.client.get("/api/search/lookup?osm_type=galaxy&osm_id=1").status_code,
            422,
        )

    def test_an_id_that_matches_nothing_is_a_404(self) -> None:
        with patch.object(
            search.httpx, "AsyncClient",
            _client_factory(lambda: _Response(200, []), []),
        ):
            response = self.client.get("/api/search/lookup?osm_type=node&osm_id=1")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
