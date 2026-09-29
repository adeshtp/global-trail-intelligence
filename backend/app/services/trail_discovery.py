"""
Supplemental semantic discovery for real hiking and trekking routes.

This module discovers names and source evidence. It never creates geometry and
never treats semantic output as verified OSM data. Postpass performs all map
identity and geometry verification in ``app.routes.discovery``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from unidecode import unidecode

from app.core.config import settings


SEARXNG_URL = (
    os.getenv("SEARXNG_URL")
    or getattr(
        settings,
        "SEARXNG_URL",
        "http://127.0.0.1:8080/search",
    )
).strip().rstrip("/")
if not SEARXNG_URL.endswith("/search"):
    SEARXNG_URL = f"{SEARXNG_URL}/search"

SEARXNG_TIMEOUT_SECONDS = max(
    3.0,
    min(
        float(
            os.getenv(
                "SEARXNG_TIMEOUT_SECONDS",
                getattr(settings, "SEARXNG_TIMEOUT_SECONDS", 12.0),
            )
        ),
        30.0,
    ),
)
SEARXNG_MAX_RESULTS = max(
    10,
    min(int(os.getenv("SEARXNG_MAX_RESULTS", "40")), 60),
)
SEARCH_QUERIES_PER_PLACE = max(
    1,
    min(
        int(
            os.getenv(
                "SEARXNG_SEARCH_QUERIES",
                getattr(settings, "SEARXNG_SEARCH_QUERIES", 2),
            )
        ),
        6,
    ),
)
# How many query formulations to try for one place. Two distinct phrasings
# of the same intent return the same pages, which is what made a place search
# appear to have no results: the budget was spent twice on one kind of page.
# Three reaches the enumeration pages AND the named-route pages, which are
# different pages with different trails in them.
SEARCH_QUERY_FLOOR = max(
    1,
    min(int(os.getenv("SEARXNG_SEARCH_QUERY_FLOOR", "3")), 6),
)
# Applied to SEARCH_QUERIES_PER_PLACE below. Two distinct phrasings of the
# same intent return the same pages, so a low configured value made a place
# search look empty simply because the whole budget went to one kind of page.
SEARCH_QUERIES_PER_PLACE = max(
    SEARCH_QUERY_FLOOR,
    SEARCH_QUERIES_PER_PLACE,
)
# Broader than the local radius. A hill station's associated destinations
# (a ridge, a waterfall, a far trailhead) routinely sit outside a town-sized
# circle even though they are what the place is known for, so a place search
# is widened to this radius while an exact-trail search is not.
PLACE_ASSOCIATION_RADIUS_M = max(
    5000.0,
    min(float(os.getenv("PLACE_ASSOCIATION_RADIUS_M", "40000.0")), 120000.0),
)
# How many characters of a result body to read when looking for names that
# the title did not carry. An enumeration page lists its trails in the body,
# and this is where those names are.
SNIPPET_NAME_SCAN_CHARS = max(
    700,
    min(int(os.getenv("SNIPPET_NAME_SCAN_CHARS", "2500")), 6000),
)
SEARCH_RESULTS_PER_QUERY = max(
    5,
    min(int(os.getenv("SEARXNG_RESULTS_PER_QUERY", "12")), 20),
)
# A listicle result names its trails in the body, not the title. The previous
# 700-character cap kept the lead paragraph and discarded every named
# destination below it, so a page titled "Top 6 Trekking Trails in Munnar"
# contributed nothing. 2500 characters is enough to cover the enumeration
# such a page is actually for.
SNIPPET_CHARS_PER_RESULT = max(
    700,
    min(int(os.getenv("SNIPPET_CHARS_PER_RESULT", "2500")), 6000),
)
SEMANTIC_CACHE_TTL_SECONDS = max(
    60.0,
    min(float(os.getenv("SEMANTIC_CACHE_TTL_SECONDS", "900")), 3600.0),
)
SEMANTIC_CACHE_MAX_ENTRIES = max(
    8,
    min(int(os.getenv("SEMANTIC_CACHE_MAX_ENTRIES", "64")), 256),
)

GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or getattr(settings, "GEMINI_API_KEY", "")
).strip()
TAVILY_API_KEY = (
    os.getenv("TAVILY_API_KEY")
    or getattr(settings, "TAVILY_API_KEY", "")
).strip()
GEMINI_TIMEOUT_SECONDS = max(
    1.0,
    float(os.getenv("GEMINI_TIMEOUT_SECONDS", "30")),
)
GEMINI_MODEL = (
    os.getenv("TRAIL_DISCOVERY_GEMINI_MODEL")
    or getattr(
        settings,
        "TRAIL_DISCOVERY_GEMINI_MODEL",
        "gemini-3.5-flash-lite",
    )
).strip()
MAX_TRAIL_CANDIDATES = max(
    10,
    min(int(os.getenv("MAX_TRAIL_CANDIDATES", "40")), 40),
)

OSM_URL_PATTERN = re.compile(
    r"openstreetmap\.org/(relation|way)/(\d+)",
    re.IGNORECASE,
)

TRAIL_WORDS = {
    "trail",
    "trails",
    "trek",
    "treks",
    "trekking",
    "hike",
    "hikes",
    "hiking",
    "route",
    "routes",
    "path",
    "paths",
    "footpath",
    "walk",
    "walking",
    "loop",
    "peak",
    "peaks",
    "summit",
    "ridge",
    "pass",
    "waterfall",
    "waterfalls",
    "falls",
    "mountain",
    "mountains",
    "hill",
    "hills",
    "mala",
    "shola",
    "circuit",
    "valley",
    "gorge",
    "canyon",
    "basecamp",
    "nature",
    "reserve",
    "sanctuary",
    "forest",
}

GENERIC_NAMES = {
    "hiking trails",
    "hiking trail",
    "trekking trails",
    "trekking trail",
    "trekking routes",
    "trekking route",
    "hiking routes",
    "hiking route",
    "hiking",
    "trekking",
    "nature trails",
    "nature trail",
    "trails",
    "trail",
    "treks",
    "trek",
    "hikes",
    "hike",
    "routes",
    "route",
    "walking routes",
    "walking route",
    "best trails",
    "top trails",
}

OUTDOOR_DESTINATION_WORDS = {
    "peak",
    "hill",
    "hills",
    "mala",
    "ridge",
    "valley",
    "waterfall",
    "waterfalls",
    "falls",
    "forest",
    "sanctuary",
    "cave",
    "lake",
    "pass",
    "gorge",
    "shola",
    "reservoir",
}

GENERIC_SEMANTIC_ONLY_WORDS = {
    "and",
    "a",
    "an",
    "the",
    "to",
    "of",
    "in",
    "near",
    "adventure",
    "adventures",
    "hidden",
    "your",
    "timings",
    "difficulty",
    "planning",
    "nature",
    "outing",
    "experience",
    "camping",
    "booking",
    "packages",
    "favourite",
    "scenic",
    "monsoon",
    "soft",
    "untouched",
    "library",
    "india",
    "top",
    "easy",
    "out",
    "day",
    "remember",
    "travels",
    "highest",
    "kashmir",
    "south",
    "destination",
    "complete",
    "beautiful",
    "major",
    "half",
    "day",
    "wonder",
    "mostly",
    "people",
    "miss",
    "guide",
    "tour",
    "trekking",
    "tours",
    "tour",
    "trials",
    "at",
    "from",
    "two",
    "there",
    "get",
    "you",
    "hikes",
    "trail",
    "trails",
    "route",
    "routes",
    "buy",
    "pants",
    "outdoor",
    "tent",
    "safari",
    "tea",
    "plantations",
    "plantation",
    "view",
    "heritage",
    "trek",
    "treks",
    "hike",
    "peak",
    "peaks",
    "hill",
    "hills",
    "waterfall",
    "waterfalls",
    "falls",
    "lake",
    "valley",
    "ridge",
    "forest",
    "mountain",
    "mountains",
    "walk",
    "walking",
}

EDITORIAL_NAME_PATTERN = re.compile(
    r"\b("
    r"best|top\s+\d+|guide|complete\s+guide|places?\s+for|"
    r"travel\s+vlog|camping\s+tours?|with\s+prices|"
    r"tourism|travel\s+mates?|resort|faq|faqs|spots?|"
    r"scenic\s+routes?|hidden\s+gems?|enthusiasts?|discover|"
    r"great\s+treks?|you\s+can'?t|might\s+not|what\s+is|"
    r"easiest|second-highest|morning\s+view|must\s+do|"
    r"complete\s+trekking|a\s+complete|ecotourism|programmes?|"
    r"where\s+clouds|with\s+reviews?|suggestions?\s+for|destinations?\s+in|"
    r"permit|route,?\s+cost|tips|for\s+nature\s+lovers|"
    r"in\s+experiences?|trekking\s+in|hiking\s+in|near\b|"
    r"around\b|buy\b|pants\b|packages?\b|booking\b|library\b|"
    r"safari\b|tent\s+camping|tea\s+plantations?|view\s+from|"
    r"highlights?|insights?|fascinating\s+facts|biggest|"
    r"unexplored|pre\s+monsoon|walking\s+map|comes\s+with|"
    r"day\s+out|walk\s+to\s+remember|second\s+highest|"
    r"tours?|trials?|wonder|mostly\s+people|people\s+miss|"
    r"difficulty|how\s+to\s+plan|planning\s+to|route\s+\d+|"
    r"hidden\s+treks|kickstart|timings?|half\s+day|"
    r"beautiful|major\s+waterfalls|trek\s+complete|and\s+stay"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OsmReference:
    type: str
    id: int
    from_source_url: bool = False


@dataclass(frozen=True)
class TrailSource:
    title: str | None
    url: str | None


@dataclass
class DiscoveredTrail:
    name: str
    aliases: list[str] = field(default_factory=list)
    osm_references: list[OsmReference] = field(default_factory=list)
    sources: list[TrailSource] = field(default_factory=list)
    location_context: str | None = None


@dataclass
class TrailDiscoveryResult:
    place: str
    trails: list[DiscoveredTrail]
    agent_available: bool
    provider: str
    provider_status: str = "unavailable"
    search_result_count: int = 0
    error: str | None = None

    # What actually happened, per stage. A single "degraded" status is not
    # diagnosable: it does not say which search backend answered, whether the
    # model was reached, or why a fallback fired. This carries the real
    # observed facts so a failure can be told apart from an empty result.
    # It never contains credentials.
    diagnostics: dict[str, Any] = field(
        default_factory=dict
    )


_SEMANTIC_CACHE: OrderedDict[
    str,
    tuple[float, TrailDiscoveryResult],
] = OrderedDict()
_SEMANTIC_INFLIGHT: dict[
    str,
    asyncio.Task[TrailDiscoveryResult],
] = {}


def _normalise_text(value: Any) -> str:
    text = unidecode(
        unicodedata.normalize("NFKC", str(value or ""))
    ).strip().casefold()
    text = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", text)


def _name_key(value: Any) -> str:
    text = _normalise_text(value).replace("&", " and ")
    return " ".join(
        re.findall(r"[\w]+", text, flags=re.UNICODE)
    )


def _tokens(value: Any) -> set[str]:
    return set(_name_key(value).split())


def _clean_candidate(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[-*•–—]+\s*", "", text)
    text = re.sub(r"^\d+[.)]\s*", "", text)
    text = re.sub(r"^#+\s*", "", text)
    text = text.strip(" \t\r\n:;,.\"'`()[]{}")
    return re.sub(r"\s+", " ", text)


def _extract_entity_name(value: str) -> str:
    text = _clean_candidate(value)
    route_match = re.search(
        r"^(?:trekking|hiking)\s+(?:route\s+to|to)\s+(.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if route_match:
        text = _clean_candidate(route_match.group(1))
        text = re.split(
            r"\s+in\s+the\b|[,.]",
            text,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
    text = re.split(
        r"\s+in\s+[^,]+$",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = re.sub(
        r"^[\w]+['’]s\s+",
        "",
        text,
        flags=re.UNICODE,
    )
    text = re.split(
        r"\bguide\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    parts = [
        _clean_candidate(part)
        for part in re.split(r"\s*[|,:.]\s*", text)
        if _clean_candidate(part)
    ]
    if _tokens(text) & OUTDOOR_DESTINATION_WORDS:
        text = re.split(
            r"\s+in\s+[^,]+$",
            text,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        parts = [
            _clean_candidate(part)
            for part in re.split(r"\s*[|,:.]\s*", text)
            if _clean_candidate(part)
        ]
    if len(parts) < 2:
        return _clean_candidate(text)
    trail_parts = [
        part
        for part in parts
        if _tokens(part) & TRAIL_WORDS
        and not EDITORIAL_NAME_PATTERN.search(part)
    ]
    return _clean_candidate(trail_parts[0] if trail_parts else text)


def _looks_like_candidate(
    text: str,
    place: str,
    *,
    has_osm_reference: bool = False,
    has_verified_source_reference: bool = False,
) -> bool:
    candidate = _clean_candidate(text)
    if not candidate or not (3 <= len(candidate) <= 160):
        return False

    candidate_key = _name_key(candidate)
    if not candidate_key or candidate_key in GENERIC_NAMES:
        return False

    if re.match(
        r"^(?:\d+\s+)?(?:best|top|great)\b",
        candidate_key,
    ):
        return False
    if re.match(r"^\d+\s+\w+", candidate_key):
        return False

    if EDITORIAL_NAME_PATTERN.search(candidate):
        return False
    candidate_token_set = set(candidate_key.split())
    if (
        re.search(
            r"\b(trekking|hiking)\b",
            candidate,
            flags=re.IGNORECASE,
        )
        and not candidate_token_set & OUTDOOR_DESTINATION_WORDS
        and not has_verified_source_reference
    ):
        return False
    if (
        not candidate_token_set & OUTDOOR_DESTINATION_WORDS
        and candidate_token_set.issubset(GENERIC_SEMANTIC_ONLY_WORDS)
        and not has_verified_source_reference
    ):
        return False
    if (
        re.search(
            r"\b(trekking|hiking)\s+(trails?|routes?)\b",
            candidate,
            flags=re.IGNORECASE,
        )
        and not candidate_token_set & OUTDOOR_DESTINATION_WORDS
        and not has_osm_reference
    ):
        return False
    if "™" in candidate or "@" in candidate:
        return False

    place_key = _name_key(place)
    candidate_tokens = set(candidate_key.split())
    place_tokens = set(place_key.split())
    distinctive_tokens = (
        candidate_tokens
        - place_tokens
        - GENERIC_SEMANTIC_ONLY_WORDS
    )
    if not distinctive_tokens and not has_verified_source_reference:
        has_named_route_word = bool(
            re.search(
                r"\b(trek|hike|trail)\b",
                candidate,
                flags=re.IGNORECASE,
            )
        )
        starts_with_place = bool(
            place_key
            and candidate_key.startswith(place_key)
        )
        if not (has_named_route_word and starts_with_place):
            return False
        if re.search(
            r"\b(adventure|trekking|hiking|experience|camping)\b",
            candidate,
            flags=re.IGNORECASE,
        ):
            return False

    # A place name by itself can still be a real trail name (for example a
    # mountain search), but a bare place with no route evidence is not useful
    # unless Gemini supplied a concrete OSM identity to verify.
    if candidate_key == place_key and not has_osm_reference:
        return False

    if (
        place_tokens
        and candidate_tokens == place_tokens
        and not has_osm_reference
    ):
        return False

    return True


def _canonical_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw

    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            parts.query,
            "",
        )
    )


def _extract_osm_reference(
    text: str | None,
    *,
    from_source_url: bool = False,
) -> OsmReference | None:
    if not text:
        return None
    match = OSM_URL_PATTERN.search(text)
    if not match:
        return None
    try:
        osm_id = int(match.group(2))
    except (TypeError, ValueError):
        return None
    if osm_id <= 0:
        return None
    return OsmReference(
        type=match.group(1).lower(),
        id=osm_id,
        from_source_url=from_source_url,
    )


def _deduplicate_references(
    references: list[OsmReference],
) -> list[OsmReference]:
    result: list[OsmReference] = []
    seen: set[tuple[str, int]] = set()
    for reference in references:
        key = (reference.type, reference.id)
        if key in seen:
            continue
        seen.add(key)
        result.append(reference)
    return result


def _search_queries(place: str) -> list[str]:
    place_key = _clean_candidate(place)
    if not place_key:
        return []

    quoted = f'"{place_key}"'

    # Ordered by what each is good at finding, because this list is truncated
    # to SEARCH_QUERIES_PER_PLACE and only the leading entries ever run. The
    # previous ordering led with two near-identical generic phrasings, so the
    # entire query budget went to one kind of page.
    #
    # The enumeration phrasings ("top N trails in X") are the highest-yield
    # queries for a place search, because a single such page names many real
    # trails in its body. They lead. The named-route phrasings follow, and
    # they are what an exact-trail query needs, since they return a page per
    # trail rather than a list of them.
    return [
        f"{quoted} hiking trails trekking routes",
        f"{quoted} best trekking trails list",
        f"{quoted} trekking route to trail",
        f"{quoted} trek trail peak waterfall",
        f"{quoted} waterfall ridge valley trail",
        f"{quoted} lesser known trekking trails",
    ][:SEARCH_QUERIES_PER_PLACE]


async def _search_one_searxng_query(
    client: httpx.AsyncClient,
    query: str,
) -> list[dict[str, Any]]:
    response = await client.get(
        SEARXNG_URL,
        params={
            "q": query,
            "format": "json",
            "categories": "general",
            "language": "en",
            "safesearch": 1,
            "pageno": 1,
        },
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []

    return [
        result
        for result in results[:SEARCH_RESULTS_PER_QUERY]
        if isinstance(result, dict)
    ]


async def _run_searxng(place: str) -> list[dict[str, Any]]:
    queries = _search_queries(place)
    if not queries:
        return []

    transport = httpx.AsyncHTTPTransport(retries=1)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(SEARXNG_TIMEOUT_SECONDS),
        headers={
            "User-Agent": "GoBeyond/1.0 (outdoor trail intelligence)",
            "Accept": "application/json",
        },
        follow_redirects=True,
        transport=transport,
    ) as client:
        responses = await asyncio.gather(
            *[
                _search_one_searxng_query(client, query)
                for query in queries
            ],
            return_exceptions=True,
        )

        if not any(
            isinstance(response, list) and response
            for response in responses
        ):
            try:
                responses.append(
                    await _search_one_searxng_query(
                        client,
                        f"{place} hiking trail",
                    )
                )
            except Exception:
                pass

    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for response in responses:
        if isinstance(response, Exception):
            continue
        for result in response:
            url = _canonical_url(result.get("url"))
            key = url or json.dumps(
                result,
                sort_keys=True,
                ensure_ascii=False,
            )
            if key in seen_urls:
                continue
            seen_urls.add(key)
            merged.append(result)
            if len(merged) >= SEARXNG_MAX_RESULTS:
                return merged

    return merged


async def _run_tavily(
    place: str,
) -> list[dict[str, Any]]:
    if not TAVILY_API_KEY:
        return []

    query = f"{place} hiking trails trekking routes"
    try:
        transport = httpx.AsyncHTTPTransport(retries=1)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            transport=transport,
            headers={
                "User-Agent": "GoBeyond/1.0 (outdoor trail intelligence)",
            },
        ) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 20,
                    "include_answer": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return []

    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return []
    return [
        {
            "title": str(result.get("title") or ""),
            "url": str(result.get("url") or ""),
            "content": str(result.get("content") or ""),
        }
        for result in raw_results
        if isinstance(result, dict)
        and str(result.get("url") or "").startswith(("http://", "https://"))
    ][:20]


def _build_gemini_input(
    place: str,
    results: list[dict[str, Any]],
) -> str:
    rows: list[str] = []
    for index, result in enumerate(results, start=1):
        title = str(result.get("title") or "").strip()
        url = str(result.get("url") or "").strip()
        content = re.sub(
            r"\s+",
            " ",
            str(result.get("content") or "").strip(),
        )
        # The snippet length was capped at 700 characters, which for a
        # listicle result usually holds the lead paragraph and cuts off
        # every named destination further down the page. The cap exists to
        # bound prompt size, so it is raised rather than removed, and each
        # result is additionally truncated only if it alone still exceeds
        # the total budget.
        if len(content) > SNIPPET_CHARS_PER_RESULT:
            content = content[:SNIPPET_CHARS_PER_RESULT] + "..."
        rows.append(
            f"RESULT {index}\nTITLE: {title}\nURL: {url}\nSNIPPET: {content}"
        )

    return (
        "Extract real named hiking, trekking, mountain, peak, ridge, valley, "
        "waterfall, forest, or nature-route entities from the supplied live web "
        "search evidence.\n"
        f'Searched place: "{place}"\n\n'
        "Rules:\n"
        "- A candidate must be supported by at least one supplied result.\n"
        "- Do not return rankings, article titles, hotels, tours, prices, or generic headings.\n"
        "- Preserve genuine alternate names and spellings in aliases.\n"
        "- A simple place or local-language name may be a valid route name; do not require an English trail keyword.\n"
        "- Do not invent OSM IDs. IDs explicitly returned by the extraction must still be verified against Postpass later.\n"
        "- If a supplied URL is an OpenStreetMap relation or way URL, preserve that exact ID in the corresponding field.\n"
        f"- Return at most {MAX_TRAIL_CANDIDATES} distinct candidates.\n\n"
        "Return JSON only using this shape:\n"
        '{"trails":[{"name":"...","aliases":[],"osm_relation_id":null,'
        '"osm_way_id":null,"source_urls":[]}]}\n\n'
        + "\n\n".join(rows)
    )


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "trails": {
            "type": "array",
            "maxItems": MAX_TRAIL_CANDIDATES,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "osm_relation_id": {
                        "type": "integer",
                        "nullable": True,
                    },
                    "osm_way_id": {
                        "type": "integer",
                        "nullable": True,
                    },
                    "source_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["name"],
            },
        }
    },
    "required": ["trails"],
}


async def _run_gemini(
    place: str,
    results: list[dict[str, Any]],
) -> list[DiscoveredTrail]:
    if not GEMINI_API_KEY or not results:
        return []

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    try:
        # Bounded: an extraction that never answers must fall back to the
        # deterministic candidates rather than hold the whole search open.
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=_build_gemini_input(place, results),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_RESPONSE_SCHEMA,
                    temperature=0.1,
                ),
            ),
            timeout=GEMINI_TIMEOUT_SECONDS,
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    text = getattr(response, "text", None) or "{}"
    payload = json.loads(text)
    raw_trails = payload.get("trails") if isinstance(payload, dict) else None
    if not isinstance(raw_trails, list):
        return []

    source_lookup: dict[str, TrailSource] = {}
    source_evidence: dict[str, str] = {}
    for result in results:
        url = _canonical_url(result.get("url"))
        if not url:
            continue
        title = str(result.get("title") or "").strip()
        content = str(result.get("content") or "").strip()
        source_lookup[url] = TrailSource(
            title=title or None,
            url=url,
        )
        source_evidence[url] = f"{title} {content}"

    place_key = _name_key(place)
    place_tokens = set(place_key.split())

    def place_supported(text: str) -> bool:
        text_key = _name_key(text)
        if not text_key:
            return False
        return (
            place_key in text_key
            or bool(place_tokens & set(text_key.split()))
        )

    trails: list[DiscoveredTrail] = []
    seen_names: set[str] = set()

    for raw in raw_trails[:MAX_TRAIL_CANDIDATES]:
        if not isinstance(raw, dict):
            continue

        references: list[OsmReference] = []
        for field_name, reference_type in (
            ("osm_relation_id", "relation"),
            ("osm_way_id", "way"),
        ):
            try:
                reference_id = int(raw.get(field_name))
            except (TypeError, ValueError):
                continue
            if reference_id > 0:
                references.append(
                    OsmReference(
                        type=reference_type,
                        id=reference_id,
                    )
                )

        name = _clean_candidate(
            _extract_entity_name(str(raw.get("name") or ""))
        )
        references = _deduplicate_references(references)
        if not _looks_like_candidate(
            name,
            place,
            has_osm_reference=bool(references),
            has_verified_source_reference=any(
                reference.from_source_url
                for reference in references
            ),
        ):
            continue

        name_key = _name_key(name)
        if not name_key or name_key in seen_names:
            continue
        seen_names.add(name_key)

        aliases: list[str] = []
        for value in raw.get("aliases") or []:
            alias = _clean_candidate(str(value))
            alias_key = _name_key(alias)
            if alias_key and alias_key != name_key:
                aliases.append(alias)
        aliases = list(dict.fromkeys(aliases))

        sources: list[TrailSource] = []
        seen_sources: set[str] = set()
        for value in raw.get("source_urls") or []:
            source_url = _canonical_url(value)
            if not source_url:
                continue
            source = source_lookup.get(source_url)
            if source is None:
                continue
            if source_url not in seen_sources:
                seen_sources.add(source_url)
                sources.append(source)

            parsed_reference = _extract_osm_reference(
                source_url,
                from_source_url=True,
            )
            if parsed_reference is not None:
                references.append(parsed_reference)

        candidate_keys = {
            _name_key(name),
            *(_name_key(alias) for alias in aliases),
        }
        evidence_urls = set(seen_sources)
        if not evidence_urls:
            for result in results:
                title_and_content = (
                    f"{result.get('title') or ''} "
                    f"{result.get('content') or ''}"
                )
                if any(
                    key and key in _name_key(title_and_content)
                    for key in candidate_keys
                ):
                    result_url = _canonical_url(result.get("url"))
                    if result_url:
                        evidence_urls.add(result_url)
                        sources.append(
                            TrailSource(
                                title=str(result.get("title") or "").strip() or None,
                                url=result_url,
                            )
                        )

        if place_key and not any(
            place_supported(source_evidence.get(url, ""))
            for url in evidence_urls
        ) and not place_supported(" ".join([name, *aliases])):
            continue

        trails.append(
            DiscoveredTrail(
                name=name,
                aliases=aliases,
                osm_references=_deduplicate_references(references),
                sources=sources,
                location_context=place,
            )
        )

    return trails


# Capitalised runs inside a result body. Web pages name real entities this
# way, and it is the only way to recover a trail name that exists solely in
# the body text.
_CAPITALISED_RUN = re.compile(
    r"\b([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3})\b"
)

# Sentence-initial words that are capitalised for grammar, not because they
# name something. Without this, "Explore", "Looking" and "Whether" are
# extracted as trails.
_SENTENCE_LEAD_STOPWORDS = frozenset(
    {
        "a", "after", "all", "an", "and", "are", "as", "at", "be", "before",
        "best", "book", "but", "by", "check", "consider", "discover", "do",
        "during", "each", "embark", "enjoy", "explore", "for", "from",
        "get", "go", "going", "good", "great", "here", "how", "if", "in",
        "is", "it", "its", "join", "journey", "just", "know", "looking",
        "make", "many", "may", "more", "most", "much", "must", "need", "not",
        "now", "of", "on", "one", "only", "or", "pack", "perfect", "plan",
        "reach", "ready", "reaching", "read", "remember", "safety", "start",
        "take", "the", "their", "there", "these", "this", "those",
        "through", "to", "top", "try", "unlike", "until", "upon", "very",
        "visit", "want", "what", "when", "where", "which", "while", "why",
        "with", "you", "your",
    }
)

# A name that is only ever a commercial brand, transport point or built
# facility. "Shola Crown" is a trekking operator, not a trail; the word
# "shola" in it is the forest type the company is named for. These are
# recognised from the words a business or facility name is made of, not from
# any particular operator or location.
# Words that claim the name IS a route, rather than merely naming somewhere.
# A settlement or facility word alongside one of these is a trail that passes
# a landmark ("Top Station Sunrise Trek"); without one it is the facility.
_ROUTE_IDENTITY_WORDS = frozenset(
    {
        "trail", "trails", "trek", "treks", "trekking", "hike", "hikes",
        "hiking", "loop", "circuit", "route", "routes", "path", "paths",
        "footpath", "walk", "walking", "track",
    }
)

_NON_TRAIL_ENTITY_WORDS = frozenset(
    {
        "bus", "stand", "power", "house", "resort", "hotel", "homestay",
        "lodge", "cottage", "villa", "centre", "center", "academy",
        "hospital", "clinic", "temple", "church", "mosque", "dam", "tour",
        "tours", "travel", "travels", "adventure", "adventures", "camp",
        "camps", "camping", "package", "packages", "booking", "book",
        "guide", "guides", "operator", "company", "holiday", "trips",
        "trip", "crown", "palace", "estate", "tower", "market",
        "junction", "corner", "office", "bank", "atm", "parking", "gate",
        "entrance", "exit", "check", "post", "view", "roof", "bridge",
        # Settlements and transport, not routes. A hill station, a tea
        # estate and a village all sit inside a trekking area and are named
        # constantly on the pages this reads.
        "station", "village", "town", "city", "estate", "plantation",
        "factory", "warehouse", "college", "school", "university",
        "railway", "airport", "bridge", "causeway", "bungalow",
    }
)


def _names_from_snippet(
    snippet: str,
    place: str,
) -> list[str]:
    """
    Names of real entities stated inside a result body.

    Search engines answer a regional query with enumeration pages whose
    titles are editorial ("Top 6 Trekking Trails in Munnar"). Every named
    trail those pages talk about is in the body, so reading only the title
    loses the entire result. This reads the body for capitalised runs and
    keeps the ones that survive the same candidate test used for titles.

    The phrase must carry route or destination evidence of its own, or sit
    immediately beside one in the text. That requirement is what separates
    "Attukad Falls Loop" from "Backpack" or "Munnar Bus Stand", and it is why
    the word "trail" appearing once anywhere on a large page does not make
    every proper noun on it a candidate.
    """
    text = re.sub(r"\s+", " ", str(snippet or "")).strip()
    if not text:
        return []

    bounded = text[:SNIPPET_NAME_SCAN_CHARS]
    found: list[str] = []
    seen: set[str] = set()

    for match in _CAPITALISED_RUN.finditer(bounded):
        phrase = match.group(1).strip()
        tokens = _tokens(phrase)
        if not tokens:
            continue
        # A single ordinary capitalised word carries no name evidence.
        if len(phrase.split()) < 2:
            continue
        lowered = phrase.split()
        if lowered[0].casefold() in _SENTENCE_LEAD_STOPWORDS:
            continue

        # The phrase must carry route or destination evidence in ITSELF.
        # Requiring only a nearby occurrence was too weak: on an
        # enumeration page, "trail" appears once and then every capitalised
        # word for miles of text passes, which is how "Munnar Bus Stand"
        # and "Power House" were being read as trails. A real trail name
        # says what it is ("Attukad Falls Loop", "Kozhiparamba Footpath",
        # "Chembra Peak"). A name that carries no route word at all is left
        # to Gemini, whose instructions already allow a bare place name.
        if not (tokens & TRAIL_WORDS):
            continue

        cleaned = _extract_entity_name(phrase)
        key = _name_key(cleaned)
        if not key or key in seen:
            continue
        if not _looks_like_candidate(cleaned, place):
            continue
        # A name built from facility, business or settlement words is not a
        # route, even when it carries a route word because of where the
        # facility is ("Munnar Hill Station") or what it sells ("Shola Crown
        # Trails"). A genuine trail can legitimately mention a station or an
        # estate ("Top Station Sunrise Trek"), so a settlement word only
        # disqualifies when the name does not also claim to BE a route.
        if _tokens(cleaned) & _NON_TRAIL_ENTITY_WORDS and not (
            _tokens(cleaned) & _ROUTE_IDENTITY_WORDS
        ):
            continue
        if EDITORIAL_NAME_PATTERN.search(cleaned):
            continue
        seen.add(key)
        found.append(cleaned)

    return found


def _candidate_name_from_title(
    title: str,
    place: str | None = None,
) -> str | None:
    cleaned_title = re.sub(r"\s+", " ", title).strip(" |-\u2013\u2014")
    if not cleaned_title:
        return None

    parts = re.split(r"\s+[|\-\u2013\u2014]\s+", cleaned_title)
    candidates = [parts[0], *parts[1:], cleaned_title]

    for candidate in candidates:
        candidate = _clean_candidate(candidate)
        tokens = _tokens(candidate)
        if not candidate or not (3 <= len(candidate) <= 160):
            continue
        if _name_key(candidate) in GENERIC_NAMES:
            continue
        if not (tokens & TRAIL_WORDS):
            continue
        if re.search(
            r"\b(best|top)\s+\d+\b",
            candidate,
            flags=re.IGNORECASE,
        ):
            continue
        extracted = _extract_entity_name(candidate)
        if (
            place is None
            or _looks_like_candidate(extracted, place)
        ):
            return extracted
    return None


def _deterministic_candidates(
    place: str,
    results: list[dict[str, Any]],
) -> list[DiscoveredTrail]:
    """Conservative fallback when Gemini is unavailable.

    Reads the title first, then the body. Reading only the title meant a
    regional query returned almost nothing: the pages that actually
    enumerate a region's trails are titled editorially ("Top 6 Trekking
    Trails in Munnar") and keep every trail name in the body instead.

    It remains supplemental: each candidate still has to survive the
    candidate test, and is still verified against OSM by the Postpass
    pipeline before anything is presented as mapped.
    """
    candidates: list[DiscoveredTrail] = []
    seen: set[str] = set()

    def _add(
        name: str,
        title: str,
        url: str,
        reference: OsmReference | None,
    ) -> None:
        key = _name_key(name)
        if not key or key in seen:
            return
        if not _looks_like_candidate(
            name,
            place,
            has_osm_reference=reference is not None,
        ):
            return
        seen.add(key)
        candidates.append(
            DiscoveredTrail(
                name=name,
                aliases=[],
                osm_references=[reference] if reference else [],
                sources=[
                    TrailSource(
                        title=title or None,
                        url=url or None,
                    )
                ],
                location_context=place,
            )
        )

    for result in results:
        title = str(result.get("title") or "").strip()
        url = _canonical_url(result.get("url"))
        reference = _extract_osm_reference(
            url,
            from_source_url=True,
        )

        name = _candidate_name_from_title(title, place)
        if name:
            _add(name, title, url, reference)
        elif reference is not None:
            # An OpenStreetMap URL is identity evidence in its own right, so
            # such a result is not discarded merely because its title is
            # editorial.
            _add(title, title, url, reference)
        else:
            for body_name in _names_from_snippet(
                str(result.get("content") or ""),
                place,
            ):
                _add(body_name, title, url, None)

        if len(candidates) >= min(MAX_TRAIL_CANDIDATES, 20):
            break

    return candidates


def _cache_get(place_key: str) -> TrailDiscoveryResult | None:
    cached = _SEMANTIC_CACHE.get(place_key)
    if cached is None:
        return None
    created_at, result = cached
    if time.monotonic() - created_at > SEMANTIC_CACHE_TTL_SECONDS:
        _SEMANTIC_CACHE.pop(place_key, None)
        return None
    _SEMANTIC_CACHE.move_to_end(place_key)
    return result


def _cache_set(
    place_key: str,
    result: TrailDiscoveryResult,
) -> None:
    _SEMANTIC_CACHE[place_key] = (time.monotonic(), result)
    _SEMANTIC_CACHE.move_to_end(place_key)
    while len(_SEMANTIC_CACHE) > SEMANTIC_CACHE_MAX_ENTRIES:
        _SEMANTIC_CACHE.popitem(last=False)


async def _discover_uncached(
    place: str,
) -> TrailDiscoveryResult:
    # SearXNG (local, preferred) is tried first. Tavily is only ever the
    # external fallback, and is therefore never called when SearXNG answers.
    # A local SearXNG either answers or fails in milliseconds, so running it
    # in series keeps external call volume minimal.
    search_error: str | None = None
    try:
        results = await _run_searxng(place)
        if not results:
            search_error = "SearXNG returned no search results"
    except Exception as exc:
        results = []
        search_error = f"SearXNG failed: {exc}"

    used_tavily = False
    if not results:
        try:
            results = await _run_tavily(place)
        except Exception as exc:
            results = []
            search_error = (
                f"{search_error}; Tavily failed: {exc}"
                if search_error
                else f"Tavily failed: {exc}"
            )
        used_tavily = bool(results)
        if not results:
            return TrailDiscoveryResult(
                place=place,
                trails=[],
                agent_available=False,
                provider="searxng+tavily+gemini",
                provider_status="unavailable",
                search_result_count=0,
                error=(
                    search_error
                    or "Semantic search returned no results"
                ),
                diagnostics={
                    "primary_provider": "searxng",
                    "search_provider_used": None,
                    "fallback_provider": "tavily",
                    "fallback_used": False,
                    "search_failure": search_error,
                    "extraction_provider": (
                        "gemini" if GEMINI_API_KEY else None
                    ),
                    "extraction_succeeded": False,
                    "extraction_failure": (
                        None
                        if GEMINI_API_KEY
                        else "not configured"
                    ),
                    "candidates_from_search": 0,
                },
            )

    # A degraded *search backend* (SearXNG down, Tavily fallback) must NOT
    # discard a successful Gemini extraction. Only a Gemini failure itself
    # falls back to deterministic candidates.
    semantic: list[DiscoveredTrail] = []
    gemini_error: str | None = None
    gemini_succeeded = False

    if GEMINI_API_KEY:
        try:
            semantic = await _run_gemini(place, results)
            gemini_succeeded = True
        except Exception as exc:
            gemini_error = f"Gemini extraction failed: {exc}"
    else:
        gemini_error = "Gemini extraction is not configured"

    deterministic = _deterministic_candidates(place, results)
    if gemini_succeeded:
        existing_names = {
            _name_key(candidate.name)
            for candidate in semantic
        }
        for candidate in deterministic:
            candidate_key = _name_key(candidate.name)
            if candidate_key and candidate_key not in existing_names:
                semantic.append(candidate)
                existing_names.add(candidate_key)
    else:
        semantic = deterministic

    provider_status = "degraded" if used_tavily else "ok"
    if not semantic:
        provider_status = "unavailable"
    elif provider_status == "ok" and gemini_error:
        provider_status = "degraded"

    reported_errors = [
        part
        for part in (
            search_error if used_tavily else None,
            gemini_error,
        )
        if part
    ]

    return TrailDiscoveryResult(
        place=place,
        trails=semantic[:MAX_TRAIL_CANDIDATES],
        agent_available=provider_status in {"ok", "degraded"},
        provider=(
            "searxng+tavily+gemini"
            if used_tavily
            else "searxng+gemini"
        ),
        provider_status=provider_status,
        search_result_count=len(results),
        error="; ".join(reported_errors) or None,
        diagnostics={
            "primary_provider": "searxng",
            "search_provider_used": (
                "tavily" if used_tavily else "searxng"
            ),
            "fallback_provider": "tavily",
            "fallback_used": used_tavily,
            "search_failure": search_error if used_tavily else None,
            "extraction_provider": (
                "gemini" if GEMINI_API_KEY else None
            ),
            "extraction_succeeded": gemini_succeeded,
            "extraction_failure": gemini_error,
            "candidates_from_model": (
                len(semantic) if gemini_succeeded else 0
            ),
            "candidates_from_search_titles": len(deterministic),
            "candidates_total": len(semantic),
        },
    )


async def discover_trail_candidates(
    place: str,
    latitude: float,
    longitude: float,
) -> TrailDiscoveryResult:
    del latitude, longitude
    place = str(place or "").strip()
    if not place:
        return TrailDiscoveryResult(
            place=place,
            trails=[],
            agent_available=False,
            provider="searxng+gemini",
            provider_status="unavailable",
            error="No canonical place name was provided",
        )

    place_key = _name_key(place)
    cached = _cache_get(place_key)
    if cached is not None:
        return cached

    task = _SEMANTIC_INFLIGHT.get(place_key)
    if task is None:
        task = asyncio.create_task(_discover_uncached(place))
        _SEMANTIC_INFLIGHT[place_key] = task

    try:
        result = await asyncio.shield(task)
    finally:
        if task.done() and _SEMANTIC_INFLIGHT.get(place_key) is task:
            _SEMANTIC_INFLIGHT.pop(place_key, None)

    if result.provider_status in {"ok", "degraded"} and result.trails:
        _cache_set(place_key, result)
    return result
