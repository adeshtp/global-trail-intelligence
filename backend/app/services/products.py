from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import quote_plus, urlsplit

import httpx

from app.core.config import settings


logger = logging.getLogger(__name__)


TAVILY_URL = "https://api.tavily.com/search"
# Gear items are NOT truncated. Every requirement the selected route
# justified gets its own card, because silently dropping one hides gear the
# system just told the user to bring.
#
# The number of items is bounded by construction rather than by a cut-off:
# gear needs come from a fixed set, so the list cannot grow without limit.
# What actually bounds the provider is the concurrency semaphore below plus
# the endpoint rate limit, not a cap on how many items are answered.
MAX_RELATED_RESULTS = 2
# Product search follows the GEAR PRIORITY, not a category list. Essential
# preparation is searched first, optional preparation last, so a small
# provider budget is spent on what actually matters for the selected route.
GEAR_PRIORITY_RANK = {"essential": 0, "recommended": 1, "conditional": 2}

# Gear needs that cannot be bought. Searching a real shop for an offline map
# or a first-aid principle wastes a provider call and produces a misleading
# card, so they are excluded from the product request entirely.
NON_PURCHASABLE_NEEDS = {"navigation", "first_aid"}
PRODUCT_CACHE_TTL_SECONDS = max(
    300.0,
    min(float(os.getenv("PRODUCT_CACHE_TTL_SECONDS", "1800")), 86400.0),
)
PRODUCT_CACHE_MAX_ENTRIES = max(
    16,
    min(int(os.getenv("PRODUCT_CACHE_MAX_ENTRIES", "64")), 256),
)

_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_INFLIGHT: dict[str, asyncio.Task[dict[str, Any]]] = {}

_PRODUCT_URL_MARKERS = (
    "/product/",
    "/products/",
    "/item/",
    "/items/",
    "/p/",
    "/dp/",
    "/buy/",
    "/shop/",
    "/store/",
)

# Real retailer product pages are also identified by an explicit product
# identifier in the query string or in a path segment, which is how several
# large catalogues address individual products. Recognising those shapes keeps
# genuine product pages out of the "web results" list without ever turning a
# non-product page into a product.
_PRODUCT_QUERY_KEYS = (
    "pid",
    "productid",
    "product_id",
    "productid",
    "itemid",
    "item_id",
    "sku",
    "pdp",
    "variantid",
)
_PRODUCT_PATH_TOKEN = re.compile(
    r"(?:^|/)(?:p|dp|prod|item)[-_]?(?P<id>\d{4,})(?:\.|/|$)",
    re.IGNORECASE,
)

# Marketplace listing paths. eBay addresses every listing as /itm/<numeric
# id> and Etsy as /listing/<numeric id>; a numeric id in either position is
# near-certain to be a product page rather than editorial content. The numeric
# requirement is what keeps this safe: a blog post can contain the word
# "listing", but it does not address it with a bare numeric product id.
_MARKETPLACE_LISTING_TOKEN = re.compile(
    r"(?:^|/)(?:itm|listing)/(?:[^/]+/)?(?P<id>\d{4,})(?:\.|/|$)",
    re.IGNORECASE,
)

MAX_EXTERNAL_URL_LENGTH = 2048
# How many category pictures one card may consider. They are alternatives to
# pick from, not a gallery.
MAX_CATEGORY_IMAGES = 6


def safe_public_url(value: Any) -> str | None:
    """
    Return the URL only if it is a plain, safe web link.

    Everything shown to the user and everything handed to the browser as an
    image or a link goes through here. A search provider is an external
    source, so its output is untrusted: only absolute http/https URLs with a
    host, no embedded credentials, no control characters and a sane length
    are passed on. Anything else is reported as missing rather than rendered,
    so a `javascript:`, `data:` or `file:` URL can never reach the client.
    """
    text = str(value or "").strip()
    if not text or len(text) > MAX_EXTERNAL_URL_LENGTH:
        return None
    if any(ord(character) < 0x20 for character in text):
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme.casefold() not in {"http", "https"}:
        return None
    if not parts.netloc or "@" in parts.netloc:
        return None
    return text


def _has_product_identifier(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    lowered_query = parts.query.casefold()
    if any(
        f"{key}=" in lowered_query
        for key in _PRODUCT_QUERY_KEYS
    ):
        return True
    if _PRODUCT_PATH_TOKEN.search(parts.path):
        return True
    return bool(
        _MARKETPLACE_LISTING_TOKEN.search(parts.path)
    )



def _gear_items(intelligence: dict[str, Any]) -> list[dict[str, str]]:
    """
    Select the gear items worth spending a product search on.

    Ordered by the gear priority the system itself derived, skipping items
    that cannot be bought. A small, well-chosen budget beats a large
    unranked catalogue.
    """
    gear = intelligence.get("gear") or {}
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_item in gear.get("items", []):
        if not isinstance(raw_item, dict):
            continue
        need = str(raw_item.get("need") or "").strip()
        item = str(raw_item.get("item") or "").strip()
        category = str(raw_item.get("category") or "").strip()
        if not item or not category:
            continue
        if need in NON_PURCHASABLE_NEEDS:
            continue
        if item.casefold() in seen:
            continue
        seen.add(item.casefold())
        items.append(
            {
                "category": category,
                "item": item,
                "priority": str(raw_item.get("priority") or "recommended"),
                "reason": str(raw_item.get("reason") or ""),
            }
        )
    items.sort(
        key=lambda entry: (
            GEAR_PRIORITY_RANK.get(entry["priority"], 3),
            entry["item"],
        )
    )
    # No truncation: every gear item is answered with a card.
    return items


def _query_for_item(
    item: dict[str, str],
    intelligence: dict[str, Any],
) -> str:
    """
    Build a short, shop-shaped query from a prioritised gear item.

    The gear wording is written for a person and often carries a caveat
    ("..., if any of the route is walked after dark") or states a condition of
    a product rather than the product itself ("Broken-in trail shoes"). A
    search engine given that phrasing returns boot-care guides, so the caveat
    and the qualifier are stripped and the product noun is searched on its own.

    The generic category word is deliberately not added: "footwear" or
    "clothing" tells a shopping engine nothing the product noun has not
    already said, and it dilutes the query. What shapes the results is the
    product noun, the activity, and an explicit purchase intent.
    """
    condition = (
        (intelligence.get("condition") or {}).get("likelihood")
    )
    phrase = _searchable_phrase(item["item"])
    terms = [phrase, "hiking"]
    # The condition status values are exactly those the gear logic produces:
    # favorable, caution, adverse, unknown. An earlier set of "moderate" and
    # "high" could never match a real status, so this branch was dead and wet
    # routes were searched for as if they were dry.
    if str(condition or "").strip().lower() in {
        "caution",
        "adverse",
    }:
        terms.append("wet weather")
    # A plain product noun makes a general web search return buying guides and
    # editorial advice rather than product pages. An explicit purchase intent
    # is what returns real listings, so the query states it.
    terms.append("buy")
    query = " ".join(term for term in terms if term)
    return f"{query} online"


# Words that turn a gear label into a shop query rather than a sentence.
_CAVEAT_CUTOFFS = (
    ", if ",
    " if any ",
    " if the ",
    " where the ",
    " where ",
)

# A leading qualifier that describes a state of a product rather than the
# product itself, for example "Broken-in trail shoes".
_LEADING_QUALIFIER_PATTERN = re.compile(
    r"^[A-Za-z]+(?:-[A-Za-z]+)+\s+(?P<product>.+)$"
)

# A gear label can name the recorded surface rather than the product type.
# Map the surface onto the product a person would actually buy.
_SURFACE_TO_PRODUCT = {
    "earth": "trail shoes",
    "soil": "trail shoes",
    "mud": "waterproof trail shoes",
    "grass": "trail shoes",
    "gravel": "trail shoes",
    "rock": "approach shoes",
    "sand": "trail shoes",
    "wood": "trail shoes",
    "dirt": "trail shoes",
    "ground": "trail shoes",
    "snow": "insulated hiking boots",
    "paving_stones": "walking shoes",
    "asphalt": "walking shoes",
    "concrete": "walking shoes",
    "paved": "walking shoes",
    "cobblestone": "walking shoes",
    "compacted": "trail shoes",
}


def _searchable_phrase(label: str) -> str:
    text = str(label or "").strip()
    for cutoff in _CAVEAT_CUTOFFS:
        index = text.casefold().find(cutoff)
        if index > 0:
            text = text[:index]
    text = text.strip().rstrip(",")
    lowered = text.casefold()
    for surface, product in _SURFACE_TO_PRODUCT.items():
        if f"suited to {surface}" in lowered:
            return product
    # A preparation label often states a state rather than a product: "Broken-in
    # trail shoes" is an instruction about a product, and a shopping engine
    # given that phrase returns boot-care guides. The product noun is the part
    # after the qualifier.
    qualified = _LEADING_QUALIFIER_PATTERN.match(text)
    if qualified and qualified.group("product").strip():
        return qualified.group("product").strip()
    return text


# ============================================================
# PRODUCT PRESENTATION
# ============================================================
# Search engines return page titles that are SEO strings, not product names.
# The card has to look like a shopping card, so titles are trimmed while
# preserving the product identity, and the retailer is derived from the real
# destination rather than from the search provider.

# Site suffixes that add nothing to a product name.
_SITE_SUFFIXES = (
    "| sale | buy walking & hiking footwear online uk",
)

# Editorial / comparison content is a legitimate web result but it is not a
# product. These patterns are deliberately structural. Loose words like
# "best" or "buy" are NOT used, because retailer SEO uses them constantly
# ("Best Price", "Buy Online") and matching them removed real products.
_EDITORIAL_PATTERNS = (
    r"\breview\b",
    r"\breviews\b",
    r"\bbuying guide\b",
    r"\bhow to (?:choose|buy|pick)\b",
    r"\b(?:best|top)\s+\d+\s+\w+",
    r"\b(?:best|top)\b[^.]{0,40}\b20\d\d\b",
    r"\btested\b",
    r"\b\d{4}\s*:\s*tested\b",
    r"\bvs\.?\s+\w+",
    r"\bversus\b",
    r"\bcompared?\s+(?:to|with)\b",
    r"\broundup\b",
    r"\bwhat (?:is|are)\b",
    r"\bdeal of the day\b",
)


def _is_editorial(title: str, snippet: str) -> bool:
    """
    True when the page is review, guide or comparison content.

    Used only to describe the result honestly, never to reject a genuine
    product page, which is identified by its destination URL instead.
    """
    import re

    haystack = f"{title}. {snippet}"
    return any(
        re.search(pattern, haystack, re.IGNORECASE)
        for pattern in _EDITORIAL_PATTERNS
    )


# Path segments that identify a page as editorial whatever its title says.
# Whole segments only: "/poles-guide" is not "/guides/".
_EDITORIAL_URL_SEGMENTS = frozenset(
    {
        "blog",
        "blogs",
        "review",
        "reviews",
        "guide",
        "guides",
        "article",
        "articles",
        "news",
        "magazine",
        "expert-advice",
    }
)


def _is_editorial_result(result: dict[str, Any]) -> bool:
    """
    True when a result is review or guide content by title, snippet or path.

    Decides where a "Shop options" link may point. It is deliberately not
    used to reject results from the list of related web results.
    """
    if result.get("editorial"):
        return True
    try:
        path = urlsplit(str(result.get("url") or "")).path.casefold()
    except ValueError:
        return False
    return any(
        segment in _EDITORIAL_URL_SEGMENTS
        for segment in path.split("/")
    )


# Categories that are never a hiking product, however the keyword matched.
_OFF_TOPIC_MARKERS = (
    "equestrian",
    "horse",
    "equine",
    "pony",
    "stable",
    "saddle",
    "bridle",
    "veterinary",
    "vet ",
    "dog ",
    "cat ",
    "aquarium",
    "cattle",
    "livestock",
)


def _clean_title(raw: str) -> str:
    """
    Reduce a search-engine page title to a readable product name.

    Only the redundant site-name tail and obvious keyword padding are removed.
    The distinguishing part of the title is always kept, so two different
    products of the same type remain distinguishable.
    """
    text = " ".join(str(raw or "").split())
    if not text:
        return ""

    lowered = text.casefold()
    for suffix in _SITE_SUFFIXES:
        index = lowered.find(suffix)
        if index > 0:
            text = text[:index].strip(" -–—|·,")
            lowered = text.casefold()

    # Drop a trailing site name after a separator when it merely repeats
    # content already present, e.g. "... - Buy X Online | Store".
    parts = [part.strip() for part in text.split("|")]
    if len(parts) > 1:
        parts = [part for part in parts if part]
        if parts and len(parts[0]) >= 20:
            text = parts[0]

    # SEO padding that repeats the same phrase twice.
    for marker in (" - Buy ", " – Buy ", " | Buy "):
        index = text.find(marker)
        if index > 20:
            head = text[:index].strip()
            tail = text[index + len(marker):]
            if tail and tail.casefold().startswith(head[:40].casefold()):
                text = head
                break

    if len(text) > 110:
        text = text[:107].rstrip() + "…"
    return text.strip(" -–—|·,")


def _retailer_from_url(url: str) -> str:
    """
    Name the actual shop or site the link goes to.

    The search provider is deliberately NOT shown: the user should see where
    the link actually leads, not which API found it.
    """
    try:
        host = urlsplit(url).netloc
    except ValueError:
        return "Product page"
    host = host.casefold().split(":")[0]
    if not host or host.startswith("www."):
        host = host[4:] if host.startswith("www.") else host
    if not host:
        return "Product page"
    label = host.split(".")[0].replace("-", " ")
    return label[:1].upper() + label[1:]


def _clean_snippet(raw: str) -> str:
    """
    Keep a short factual description and discard raw page dumps.

    Price, review and stock fragments are removed because the source does not
    return them as reliable structured data, and reproducing them half-way
    from scraped text would be inventing commerce information.
    """
    text = " ".join(str(raw or "").split())
    if not text:
        return ""
    # Drop the "Title: ..." echo the search engine prepends.
    if text.casefold().startswith("title:"):
        text = text[6:].strip()
    for marker in ("\n", "###"):
        if marker in text:
            text = text.split(marker)[0].strip()
    # Remove commerce fragments we must not present. Scraped text often
    # contains price, rating and review counts in inconsistent shapes, so a
    # few forms are removed rather than trusting any of them.
    #
    # A bare "Price:"/"Rating:" label with its number already stripped reads as
    # commerce residue, so the labels go too. Leaving "Price: reviews" behind is
    # exactly the half-reproduced commerce fragment this is meant to prevent.
    for pattern in (
        r"\$[\d.,]+",
        r"[\(\[]?\s*\d[\d.,]*\s*(?:reviews?|ratings?|stars?)\s*[\)\]]?",
        r"\(\s*\d+\s*\)",
    ):
        text = re.sub(pattern, " ", text)
    text = re.sub(
        r"\b(?:price|prices|rating|ratings|review|reviews)\b\s*[:\-]?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = " ".join(text.split())
    if len(text) < 25:
        return ""
    return text[:220].rstrip() + ("…" if len(text) > 220 else "")



def _is_off_topic(title: str, snippet: str) -> bool:
    haystack = f"{title} {snippet}".casefold()
    return any(marker in haystack for marker in _OFF_TOPIC_MARKERS)


# Words that carry no information about what a product IS, so they cannot be
# used to decide whether a result is about that product. Activity words and
# condition words are the reason a general web search drifts: they match a
# great deal of the web and none of the product.
_NON_DISTINCTIVE_TERMS = frozenset(
    {
        "and",
        "any",
        "are",
        "bag",
        "buy",
        "cold",
        "dry",
        "for",
        "from",
        "gear",
        "hiking",
        "its",
        "mud",
        "muddy",
        "online",
        "pair",
        "rain",
        "rainy",
        "set",
        "snow",
        "the",
        "this",
        "trails",
        "warm",
        "wet",
        "weather",
        "with",
        "your",
    }
)


def item_terms(phrase: str) -> set[str]:
    """
    The words that actually identify which product is being searched for.

    "Waterproof shell hiking wet weather" carries two real terms, "waterproof"
    and "shell"; the rest is activity and condition context. Matching a result
    against these is what separates a page about the product from a page that
    merely shares a word with the query.
    """
    return {
        token
        for token in re.findall(r"[a-z0-9]+", phrase.casefold())
        if len(token) >= 4 and token not in _NON_DISTINCTIVE_TERMS
    }


def _is_about_item(
    title: str,
    snippet: str,
    phrase: str,
) -> bool:
    """
    True when the result is plausibly about the product being searched for.

    A general web search for a product routinely returns pages that share a
    word with the query and nothing else: searching for trail shoes in wet
    weather can return a weather service's own landing pages. Relaying those
    under a heading that says "web results for trail shoes" presents a page
    that is demonstrably not about trail shoes.

    The test is deliberately permissive. A result is only discarded when it
    mentions NONE of the product's own words, so anything topically adjacent
    is kept. Genuine product pages are exempt in the caller, because a real
    product page is by definition about the item even when its title is a
    brand and a colour.
    """
    terms = item_terms(phrase)
    if not terms:
        # Nothing distinctive to match on. Discarding everything would be
        # worse than showing the provider's ranking.
        return True
    haystack = f"{title} {snippet}".casefold()
    return any(
        re.search(rf"\b{re.escape(term)}", haystack) for term in terms
    )


def _search_link(query: str) -> dict[str, str]:
    return {
        "title": f"Search real results for: {query}",
        "url": (
            "https://www.google.com/search?q="
            f"{quote_plus(query)}"
        ),
        "source": "Google search link",
    }


def _is_product_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        path = parts.path.casefold()
    except ValueError:
        return False
    if any(marker in path for marker in _PRODUCT_URL_MARKERS):
        return True
    return _has_product_identifier(url)


def _image_for_result(raw: dict[str, Any]) -> str | None:
    """
    Return an image only when the provider paired it with THIS result.

    Accepted shapes, all result-scoped:

    * ``image``: a single URL string
    * ``images``: a list of URL strings or ``{"url": ...}`` objects

    A top-level response image list is deliberately ignored, because its
    ordering relative to ``results`` is not guaranteed and can pair a product
    with another seller's photograph.

    Whatever the provider supplies is still an external value, so it is put
    through the same safety check as a link: only a plain http/https image URL
    is ever handed to the browser.
    """
    single = safe_public_url(raw.get("image"))
    if single:
        return single

    nested = raw.get("images")
    if isinstance(nested, list):
        for entry in nested:
            candidate = (
                entry.get("url")
                if isinstance(entry, dict)
                else entry
            )
            validated = safe_public_url(candidate)
            if validated:
                return validated
    return None


def _cache_get(key: str) -> dict[str, Any] | None:
    cached = _CACHE.get(key)
    if cached is None:
        return None
    created_at, value = cached
    if time.monotonic() - created_at > PRODUCT_CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    _CACHE.move_to_end(key)
    return value


def _cache_set(key: str, value: dict[str, Any]) -> None:
    _CACHE[key] = (time.monotonic(), value)
    _CACHE.move_to_end(key)
    while len(_CACHE) > PRODUCT_CACHE_MAX_ENTRIES:
        _CACHE.popitem(last=False)


async def _search_web(
    query: str,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    api_key = (
        os.getenv("TAVILY_API_KEY")
        or getattr(settings, "TAVILY_API_KEY", "")
    ).strip()
    if not api_key:
        return [], [], "Product search is not configured"

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
                TAVILY_URL,
                json={
                    "api_key": api_key,
                    "query": query,
                    # Advanced search, measured live: for the same shop-shaped
                    # query it returns retailer category and brand pages
                    # where basic returns buying guides. Neither depth
                    # returns direct product pages with images for these
                    # queries, so the classification rules are unchanged —
                    # only the evidence pool is closer to shopping.
                    "search_depth": "advanced",
                    "max_results": 6,
                    "include_answer": False,
                    "include_images": True,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return [], [], "Product search is temporarily unavailable"

    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return [], [], "Product search returned an invalid response"

    # Image pairing must be trustworthy.
    #
    # Tavily can also return a top-level `images` array, but it is NOT
    # guaranteed to be index-aligned with `results`: in practice it can hand
    # back a photo of a different product from a different retailer. Pairing
    # by position therefore displays a wrong product image, which is worse
    # than showing none. Only an image the provider attaches to THIS result is
    # used; anything else is reported as unavailable.
    results: list[dict[str, Any]] = []
    rejected_unsafe = 0
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        url = safe_public_url(raw.get("url"))
        title = str(raw.get("title") or "").strip()
        if not url or not title:
            # A result whose destination is not a plain web link is never
            # rendered as a link or as an image source.
            rejected_unsafe += int(not url)
            continue
        result: dict[str, Any] = {
            "title": title,
            "url": url,
            "source": str(raw.get("source") or "Tavily"),
            "kind": (
                "product_candidate"
                if _is_product_url(url)
                else "related_web_result"
            ),
        }
        image = _image_for_result(raw)
        if image:
            result["image"] = image
        # The raw text is kept separately from the displayed snippet. Cleaning
        # removes prices, review counts and page dumps so the description stays
        # factual, but those fragments are still real words from the page, and
        # deciding whether a result is ABOUT the item should read the text the
        # provider actually returned rather than the version curated for
        # display. Judging on the cleaned snippet alone discarded results
        # whose product detail lived in the part that cleaning had trimmed.
        raw_content = str(raw.get("content") or "")
        result["_relevance_text"] = raw_content
        snippet = _clean_snippet(raw.get("content"))
        if snippet:
            result["snippet"] = snippet
        # Present a readable product name and the real destination, and
        # classify the result so the card can be labelled honestly.
        result["display_title"] = _clean_title(title)
        result["retailer"] = _retailer_from_url(url)
        result["editorial"] = _is_editorial(title, snippet)
        result["off_topic"] = _is_off_topic(title, snippet)
        results.append(result)
    if rejected_unsafe:
        logger.warning(
            "Dropped %d product result(s) with an unsafe destination URL",
            rejected_unsafe,
        )

    # ------------------------------------------------------------------
    # CATEGORY IMAGES
    #
    # The provider returns images in two legitimate places, and BOTH are
    # evidence for the gear category this query was about:
    #
    #   * a top-level `images` list, which is not index-aligned with the
    #     results, and
    #   * an `image` / `images` field attached to an individual result.
    #
    # Neither may ever illustrate a specific product, because a top-level
    # image can be a different seller's photo and a result image belongs to
    # that result alone. That is why the pairing rules for products stay
    # strict, and `test_product_images_never_pair_a_different_sellers_photo`
    # still guards it.
    #
    # They ARE legitimate for a shopping-category card, which makes no claim
    # to depict one particular product. It says "this is what waterproof
    # hiking shoes look like, here is where to buy them". Every candidate
    # came back from a search about that exact category, so using it there
    # neither fabricates nor mispairs anything. Pooling both sources means a
    # card still has a picture when the provider populated only one of them.
    # ------------------------------------------------------------------
    category_images: list[str] = []

    def collect(raw_images: Any) -> None:
        if not isinstance(raw_images, list):
            return
        for entry in raw_images:
            candidate = (
                entry.get("url")
                if isinstance(entry, dict)
                else entry
            )
            validated = safe_public_url(candidate)
            if validated and validated not in category_images:
                category_images.append(validated)
            if len(category_images) >= MAX_CATEGORY_IMAGES:
                return

    collect(payload.get("images") if isinstance(payload, dict) else None)
    if len(category_images) < MAX_CATEGORY_IMAGES:
        for result in results:
            if result.get("image"):
                collect([result["image"]])

    return results, category_images, None


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    """
    A search result with internal fields removed.

    `_relevance_text` holds untrusted page text used only to judge whether the
    result is about the requested item. It is not a description the interface
    shows, so it never leaves the backend.
    """
    return {
        key: value
        for key, value in result.items()
        if not key.startswith("_")
    }


def _build_card(
    item: dict[str, str],
    *,
    product_results: list[dict[str, Any]],
    relevant: list[dict[str, Any]],
    category_images: list[str],
    query: str,
    error: str | None,
) -> dict[str, Any]:
    """
    Produce exactly one shopping card for one gear requirement.

    Two honest modes:

    ``direct_product``
        A real product page was returned AND the provider attached an image
        to that same result. The name, picture and link all come from one
        real result, so they cannot describe different things.

    ``shopping_fallback``
        No trustworthy product page (or no paired image for one). The card
        is explicitly a category recommendation: it carries the gear item
        the route actually asked for, a category picture returned for that
        category, and a real destination to buy it. It is never presented as
        a specific product, and no title, price, rating or stock is invented.

    A gear item is never left without a card. An empty Products section is a
    worse outcome than an honest category link.
    """
    # Mode A: a real product page with an image the provider tied to it.
    for result in product_results:
        image = safe_public_url(result.get("image"))
        if not image:
            continue
        return {
            "mode": "direct_product",
            "gear_item": item["item"],
            "name": result.get("display_title") or result.get("title"),
            "image": image,
            "retailer": result.get("retailer"),
            "url": safe_public_url(result["url"]),
            "cta": "View product",
            "description": result.get("snippet") or None,
        }

    # Mode B. Prefer a real retailer destination that the provider actually
    # returned for this category, so the link is a page that was seen rather
    # than a URL shape that was guessed. A review or guide is never a place to
    # shop, however high search ranked it, so it is skipped here and, with no
    # other destination, the card falls back to a search link.
    destination = next(
        (
            result
            for result in relevant
            if safe_public_url(result.get("url"))
            and not _is_editorial_result(result)
        ),
        None,
    )
    url = (
        safe_public_url(destination["url"])
        if destination
        else _search_link(query)["url"]
    )
    retailer = (
        destination.get("retailer")
        if destination
        else None
    )
    # The card is the last gate before the browser, so the image is
    # re-checked here rather than trusting the search layer to have done it.
    image = next(
        (
            validated
            for candidate in category_images
            if (validated := safe_public_url(candidate))
        ),
        None,
    )

    return {
        "mode": "shopping_fallback",
        "gear_item": item["item"],
        "name": item["item"],
        "image": image,
        "retailer": retailer,
        "url": url,
        "cta": "Shop options",
        "description": item.get("reason") or None,
    }


async def _search_item(
    item: dict[str, str],
    intelligence: dict[str, Any],
) -> dict[str, Any]:
    query = _query_for_item(item, intelligence)
    results, category_images, error = await _search_web(query)
    phrase = _searchable_phrase(item["item"])

    # A result is presented as a PRODUCT when the destination is genuinely a
    # product page. Title wording is deliberately NOT used to reject one:
    # retailer SEO routinely contains "Best Price" and "Buy Online", so
    # filtering on those words threw away real products. Anything that is not
    # a product page is shown separately as a web result, and anything in an
    # unrelated category is dropped with a stated reason.
    usable = [result for result in results if not result.get("off_topic")]
    product_results = [
        result
        for result in usable
        if result["kind"] == "product_candidate"
    ][:3]
    # A non-product result must additionally be about the item. A product page
    # is exempt: its destination already proves what it is, and a retailer page
    # titled only with a brand and a colour would otherwise be discarded.
    #
    # The test uses the title AND the snippet. A result whose title is a bare
    # SEO heading ("Best Hiking Shoes for Men 2026") is frequently paired with
    # a snippet that names specific products, and judging on the title alone
    # discarded those while accepting vaguer ones.
    def about(result: dict[str, Any]) -> bool:
        return _is_about_item(
            result.get("title") or "",
            f"{result.get('snippet') or ''} {result.get('_relevance_text') or ''}",
            phrase,
        )

    # A result is EXCLUDED only if it genuinely fails the about test. Truncating
    # the list to the display cap must not be reported as a relevance failure:
    # a live response was labelling relevant results "not about this preparation
    # item" purely because they sat past the third slot, which made the rejection
    # list contradict the results shown beside it.
    product_urls = {result["url"] for result in product_results}
    relevant = [
        result
        for result in usable
        if result["url"] not in product_urls and about(result)
    ]
    web_results = relevant[:MAX_RELATED_RESULTS]
    rejected = [
        {
            "title": result.get("display_title") or result.get("title"),
            "url": result.get("url"),
            "reason": "unrelated product category",
        }
        for result in results
        if result.get("off_topic")
    ][:3]
    # Anything not shown and not counted as relevant is reported with a reason,
    # and the reason must be the real one. A result dropped only because the
    # display list was full is named as such, so the rejection list can never
    # imply the provider returned nothing usable when it did.
    overflow = [
        {
            "title": result.get("display_title") or result.get("title"),
            "url": result.get("url"),
            "reason": "relevant, beyond the number shown per item",
        }
        for result in relevant[MAX_RELATED_RESULTS:]
    ]
    shown_urls = product_urls | {result["url"] for result in relevant}
    rejected.extend(overflow)
    rejected.extend(
        {
            "title": result.get("display_title") or result.get("title"),
            "url": result.get("url"),
            "reason": "not about this preparation item",
        }
        for result in usable
        if result["url"] not in shown_urls
    )

    if not product_results and not error and not web_results:
        status = "unavailable"
        message = (
            "The search returned nothing relevant to this preparation item."
        )
    elif product_results:
        status = "ok"
        message = None
    else:
        status = "search_link"
        message = (
            "No product page was found for this item. The links below are "
            "web search results, not products."
        )

    card = _build_card(
        item,
        product_results=product_results,
        relevant=relevant,
        category_images=category_images,
        query=query,
        error=error,
    )

    return {
        "category": item["category"],
        "item": item["item"],
        "priority": item["priority"],
        "reason": item["reason"],
        "query": query,
        "status": status,
        # Every gear item carries exactly one card, so the Products section
        # can never show a requirement with nothing to act on.
        "card": card,
        "has_direct_product": card["mode"] == "direct_product",
        # The internal relevance text is stripped before the response leaves
        # the backend. It exists only to decide relevance and must not be
        # shipped: it is untrusted scraped page content.
        "product_results": [
            _public_result(result) for result in product_results
        ],
        "related_results": [
            _public_result(result) for result in web_results
        ],
        "rejected_results": rejected,
        "search_link": _search_link(query),
        "result_source": (
            "live_product_search"
            if product_results
            else "web_search"
            if not error
            else "provider_unavailable"
        ),
        "image_available": any(
            result.get("image") for result in product_results
        ),
        "message": message,
    }


async def _fetch_products_uncached(
    intelligence: dict[str, Any],
) -> dict[str, Any]:
    items = _gear_items(intelligence)
    if not items:
        return {
            "status": "unavailable",
            "provider": "none",
            "groups": [],
            "message": "No route-derived gear items are available.",
        }

    semaphore = asyncio.Semaphore(3)

    async def bounded(item: dict[str, str]) -> dict[str, Any]:
        async with semaphore:
            return await _search_item(item, intelligence)

    groups = await asyncio.gather(
        *(bounded(item) for item in items)
    )
    product_count = sum(
        len(group["product_results"])
        for group in groups
    )
    provider_failed = all(
        group["result_source"] == "provider_unavailable"
        for group in groups
    )
    if provider_failed:
        # The provider never answered. Saying "search links" here would dress
        # a failure up as a result, so the state is reported as unavailable
        # and the search links are still offered as a manual fallback.
        status = "unavailable"
        message = (
            "The product search provider is unavailable, so no product page "
            "could be checked. The links below are plain search links, not "
            "verified product pages."
        )
    elif product_count:
        status = "ok"
        message = None
    else:
        status = "search_link"
        message = (
            "No verified product page was returned; real search links are "
            "provided for each gear item."
        )
    return {
        "status": status,
        "provider": "tavily",
        "groups": list(groups),
        "message": message,
    }


async def discover_products(
    intelligence: dict[str, Any],
) -> dict[str, Any]:
    items = _gear_items(intelligence)
    key = "|".join(
        f"{item['category']}:{item['item']}"
        for item in items
    )
    if not key:
        return await _fetch_products_uncached(intelligence)

    cached = _cache_get(key)
    if cached is not None:
        return cached

    task = _INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(
            _fetch_products_uncached(intelligence)
        )
        _INFLIGHT[key] = task
    try:
        result = await asyncio.shield(task)
    finally:
        if task.done() and _INFLIGHT.get(key) is task:
            _INFLIGHT.pop(key, None)
    _cache_set(key, result)
    return result
