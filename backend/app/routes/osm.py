import math
import re
from collections import Counter, defaultdict, deque
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query


router = APIRouter(
    prefix="/api/osm",
    tags=["OpenStreetMap"],
)


# ============================================================
# CONFIGURATION
# ============================================================

OVERPASS_URLS = [
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]


HEADERS = {
    "User-Agent": (
        "TerraPath/0.1 "
        "(educational outdoor intelligence project)"
    ),
}


GENERIC_NAMES = {
    "path",
    "trail",
    "way",
    "route",
    "footway",
    "track",
    "footpath",
    "walkway",
    "outdoor path",
    "outdoor track",
    "hiking path",
    "unnamed trail",
    "unnamed hiking trail",
    "connected trail network",
    "hiking path network",
    "hiking trail",
    "outdoor trail",
}


ROAD_LIKE_PATTERN = re.compile(
    r"\b("
    r"road|rd|street|st|lane|ln|avenue|ave|"
    r"drive|dr|highway|hwy|bypass|junction|jct|"
    r"motorway|expressway|school|college|hostel|"
    r"campus|residential|bus stand|market|parking|"
    r"estate road"
    r")\b",
    re.IGNORECASE,
)


TRAIL_NAME_PATTERN = re.compile(
    r"\b("
    r"trail|trek|hike|hiking|path|footpath|nature|"
    r"forest|mountain|peak|summit|waterfall|falls|"
    r"viewpoint|ridge|pass|gorge|wildlife|"
    r"sanctuary|reserve|plantation|walk"
    r")\b",
    re.IGNORECASE,
)


TRAIL_DESIGNATION_PATTERN = re.compile(
    r"trail|trek|hike|hiking|footpath|nature|walk",
    re.IGNORECASE,
)


UNPAVED_SURFACES = {
    "earth",
    "ground",
    "dirt",
    "mud",
    "sand",
    "gravel",
    "fine_gravel",
    "pebblestone",
    "unpaved",
    "grass",
    "wood",
    "woodchips",
    "compacted",
    "rock",
}


PAVED_SURFACES = {
    "asphalt",
    "concrete",
    "paved",
    "cement",
    "paving_stones",
    "sett",
}


NON_TRAIL_FOOTWAYS = {
    "sidewalk",
    "crossing",
    "alley",
}


SEARCH_STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "near",
    "at",
    "to",
    "for",
    "and",
    "or",
    "on",
    "trail",
    "trek",
    "hike",
    "hiking",
    "route",
    "path",
    "mountain",
    "peak",
    "hill",
    "location",
    "place",
}


# ============================================================
# TEXT UTILITIES
# ============================================================

def normalize_name(value: Any) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip().lower()


def tokenize_search_text(value: Any) -> list[str]:
    normalized = re.sub(
        r"[^a-z0-9]+",
        " ",
        normalize_name(value),
    )

    return [
        token
        for token in normalized.split()
        if len(token) >= 2
        and token not in SEARCH_STOPWORDS
    ]


def meaningful_name(value: Any) -> bool:
    if not value:
        return False

    name = normalize_name(value)

    return (
        bool(name)
        and name not in GENERIC_NAMES
    )


def road_like_name(value: Any) -> bool:
    return bool(value) and bool(
        ROAD_LIKE_PATTERN.search(
            str(value)
        )
    )


def trailish_name(value: Any) -> bool:
    if not meaningful_name(value):
        return False

    if road_like_name(value):
        return False

    return bool(
        TRAIL_NAME_PATTERN.search(
            str(value)
        )
    )


def text_relevance_score(
    candidate_name: Any,
    search_query: Any,
    location_name: Any,
) -> tuple[int, list[str]]:

    name = normalize_name(candidate_name)

    if not meaningful_name(name):
        return 0, []

    query_normalized = normalize_name(
        search_query
    )

    score = 0
    reasons: list[str] = []

    if (
        query_normalized
        and name == query_normalized
    ):
        score += 80

        reasons.append(
            "exact search-name match"
        )

    elif (
        query_normalized
        and query_normalized in name
    ):
        score += 55

        reasons.append(
            "search phrase match"
        )

    query_tokens = tokenize_search_text(
        search_query
    )

    matched_query_tokens = [
        token
        for token in query_tokens
        if token in name
    ]

    if matched_query_tokens:

        score += min(
            45,
            len(matched_query_tokens) * 18,
        )

        reasons.append(
            "search terms match"
        )

    location_tokens = tokenize_search_text(
        location_name
    )

    matched_location_tokens = [
        token
        for token in location_tokens
        if token in name
    ]

    if matched_location_tokens:

        score += min(
            25,
            len(matched_location_tokens) * 8,
        )

        reasons.append(
            "resolved place-name match"
        )

    return score, reasons


# ============================================================
# GEOGRAPHIC UTILITIES
# ============================================================

def haversine_distance_km(
    coord1: list[float],
    coord2: list[float],
) -> float:

    lon1, lat1 = coord1
    lon2, lat2 = coord2

    earth_radius = 6371.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    delta_phi = math.radians(
        lat2 - lat1
    )

    delta_lambda = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(delta_lambda / 2) ** 2
    )

    return (
        earth_radius
        * 2
        * math.atan2(
            math.sqrt(a),
            math.sqrt(1 - a),
        )
    )


def coordinate_distance_km(
    coordinates: list[list[float]],
) -> float:

    if len(coordinates) < 2:
        return 0.0

    return sum(
        haversine_distance_km(
            coordinates[index],
            coordinates[index + 1],
        )
        for index in range(
            len(coordinates) - 1
        )
    )


def way_coordinates(
    way: dict[str, Any],
) -> list[list[float]]:

    coordinates: list[list[float]] = []

    for point in (
        way.get("geometry") or []
    ):

        try:
            lon = float(point["lon"])
            lat = float(point["lat"])

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            continue

        if (
            math.isfinite(lon)
            and math.isfinite(lat)
        ):
            coordinates.append(
                [lon, lat]
            )

    return coordinates


def get_way_length_km(
    way: dict[str, Any],
) -> float:

    return coordinate_distance_km(
        way_coordinates(way)
    )


def distance_to_search_km(
    coordinates: list[list[float]],
    latitude: float,
    longitude: float,
) -> float:

    if not coordinates:
        return float("inf")

    search_point = [
        longitude,
        latitude,
    ]

    return min(
        haversine_distance_km(
            point,
            search_point,
        )
        for point in coordinates
    )


# ============================================================
# OSM RELATION UTILITIES
# ============================================================

def relation_member_ids(
    relation: dict[str, Any],
) -> list[int]:

    ids: list[int] = []
    seen: set[int] = set()

    for member in (
        relation.get("members") or []
    ):

        if member.get("type") != "way":
            continue

        ref = member.get("ref")

        try:
            way_id = int(ref)

        except (
            TypeError,
            ValueError,
        ):
            continue

        if (
            way_id
            and way_id not in seen
        ):
            seen.add(way_id)
            ids.append(way_id)

    return ids


# ============================================================
# HIKING EVIDENCE
# ============================================================

def way_hiking_evidence(
    way: dict[str, Any],
    hiking_relation_way_ids: set[int],
    foot_relation_way_ids: set[int],
) -> tuple[int, list[str]]:

    tags = way.get("tags") or {}

    way_id = int(
        way.get("id") or 0
    )

    highway = tags.get("highway")

    score = 0
    reasons: list[str] = []

    # --------------------------------------------------------
    # Relation membership
    # --------------------------------------------------------

    if way_id in hiking_relation_way_ids:

        score += 80

        reasons.append(
            "hiking route member"
        )

    elif way_id in foot_relation_way_ids:

        score += 40

        reasons.append(
            "walking route member"
        )

    # --------------------------------------------------------
    # Difficulty
    # --------------------------------------------------------

    if tags.get("sac_scale"):

        score += 45

        reasons.append(
            "difficulty tagged"
        )

    # --------------------------------------------------------
    # Trail visibility
    # --------------------------------------------------------

    if tags.get("trail_visibility"):

        score += 25

        reasons.append(
            "trail visibility tagged"
        )

    # --------------------------------------------------------
    # Designation
    # --------------------------------------------------------

    designation = str(
        tags.get("designation") or ""
    )

    if TRAIL_DESIGNATION_PATTERN.search(
        designation
    ):

        score += 25

        reasons.append(
            "trail designation"
        )

    # --------------------------------------------------------
    # Informal
    # --------------------------------------------------------

    if tags.get("informal") in {
        "yes",
        "true",
        "1",
    }:

        score += 10

        reasons.append(
            "informal path"
        )

    # --------------------------------------------------------
    # Trail-like name
    # --------------------------------------------------------

    if trailish_name(
        tags.get("name")
    ):

        score += 35

        reasons.append(
            "trail-like name"
        )

    # --------------------------------------------------------
    # Network / operator
    # --------------------------------------------------------

    if (
        tags.get("network")
        or tags.get("operator")
    ):

        score += 8

        reasons.append(
            "network/operator"
        )

    # --------------------------------------------------------
    # Walking access
    # --------------------------------------------------------

    if tags.get("foot") in {
        "designated",
        "yes",
    }:

        score += 12

        reasons.append(
            "designated for walking"
        )

    # --------------------------------------------------------
    # Highway
    # --------------------------------------------------------

    if highway == "path":
        score += 5

    elif highway == "footway":
        score += 3

    # --------------------------------------------------------
    # Surface
    # --------------------------------------------------------

    surface = normalize_name(
        tags.get("surface")
    )

    if surface in UNPAVED_SURFACES:

        score += 12

        reasons.append(
            "unsealed surface"
        )

    elif surface in PAVED_SURFACES:

        score -= 8

    # --------------------------------------------------------
    # Strong penalty
    # --------------------------------------------------------

    if (
        highway == "track"
        and surface in PAVED_SURFACES
    ):
        score -= 35

    return score, reasons


# ============================================================
# BASE ELIGIBILITY
# ============================================================

def is_way_base_eligible(
    way: dict[str, Any],
) -> bool:

    tags = way.get("tags") or {}

    highway = tags.get("highway")

    if highway not in {
        "path",
        "footway",
        "track",
        "steps",
    }:
        return False

    access = normalize_name(
        tags.get("access")
    )

    if access in {
        "no",
        "private",
    }:
        return False

    footway_type = normalize_name(
        tags.get("footway")
    )

    if (
        highway == "footway"
        and footway_type
        in NON_TRAIL_FOOTWAYS
    ):
        return False

    name = tags.get("name")

    if (
        road_like_name(name)
        and not trailish_name(name)
    ):
        return False

    coordinates = way_coordinates(way)

    if len(coordinates) < 2:
        return False

    length_km = coordinate_distance_km(
        coordinates
    )

    if length_km < 0.05:
        return False

    surface = normalize_name(
        tags.get("surface")
    )

    if (
        highway == "track"
        and surface in PAVED_SURFACES
    ):
        return False

    return True


# ============================================================
# STANDALONE WAY STRENGTH
# ============================================================

def is_way_strong_enough(
    way: dict[str, Any],
    hiking_relation_way_ids: set[int],
    foot_relation_way_ids: set[int],
) -> bool:

    if not is_way_base_eligible(way):
        return False

    tags = way.get("tags") or {}

    highway = tags.get("highway")
    name = tags.get("name")

    length_km = get_way_length_km(
        way
    )

    evidence_score, _ = way_hiking_evidence(
        way,
        hiking_relation_way_ids,
        foot_relation_way_ids,
    )

    way_id = int(
        way.get("id") or 0
    )

    # Explicit hiking relation member
    if way_id in hiking_relation_way_ids:
        return length_km >= 0.20

    # Strong evidence
    if evidence_score >= 45:
        return length_km >= 0.25

    # Trail-like named path
    if trailish_name(name):
        return length_km >= 0.35

    surface = normalize_name(
        tags.get("surface")
    )

    # Unpaved paths
    if highway in {
        "path",
        "footway",
    }:

        return (
            surface in UNPAVED_SURFACES
            and length_km >= 0.60
        )

    # Tracks require longer distance
    if highway == "track":

        return (
            surface in UNPAVED_SURFACES
            and length_km >= 1.00
        )

    if highway == "steps":

        return evidence_score >= 45

    return False


# ============================================================
# GRAPH UTILITIES
# ============================================================

def build_way_graph(
    ways: list[dict[str, Any]],
) -> dict[int, set[int]]:

    node_to_ways: dict[
        int,
        list[int],
    ] = defaultdict(list)

    graph: dict[
        int,
        set[int],
    ] = {}

    for way in ways:

        way_id = int(
            way.get("id") or 0
        )

        if not way_id:
            continue

        graph.setdefault(
            way_id,
            set(),
        )

        for node_id in (
            way.get("nodes") or []
        ):

            try:
                node_to_ways[
                    int(node_id)
                ].append(way_id)

            except (
                TypeError,
                ValueError,
            ):
                continue

    for connected_ids in node_to_ways.values():

        unique_ids = list(
            dict.fromkeys(
                connected_ids
            )
        )

        if len(unique_ids) < 2:
            continue

        for way_id in unique_ids:

            graph[way_id].update(
                other_id
                for other_id in unique_ids
                if other_id != way_id
            )

    return graph


def find_connected_components(
    graph: dict[int, set[int]],
) -> list[list[int]]:

    visited: set[int] = set()

    components: list[list[int]] = []

    for start in graph:

        if start in visited:
            continue

        queue = deque([start])

        visited.add(start)

        component: list[int] = []

        while queue:

            current = queue.popleft()

            component.append(current)

            for neighbour in graph.get(
                current,
                set(),
            ):

                if neighbour in visited:
                    continue

                visited.add(neighbour)

                queue.append(neighbour)

        components.append(component)

    return components


def component_is_simple_chain(
    component_ids: list[int],
    graph: dict[int, set[int]],
) -> bool:

    if len(component_ids) < 2:
        return False

    component_set = set(component_ids)

    edge_count = 0
    maximum_degree = 0

    for way_id in component_ids:

        neighbours = (
            graph.get(
                way_id,
                set(),
            )
            & component_set
        )

        degree = len(neighbours)

        maximum_degree = max(
            maximum_degree,
            degree,
        )

        edge_count += degree

    edge_count //= 2

    if edge_count != (
        len(component_ids) - 1
    ):
        return False

    if maximum_degree > 2:
        return False

    return True


# ============================================================
# METADATA UTILITIES
# ============================================================

def most_common_value(
    values: list[Any],
) -> Any:

    clean = [
        value
        for value in values
        if value not in {
            None,
            "",
        }
    ]

    if not clean:
        return None

    return Counter(
        clean
    ).most_common(1)[0][0]


def aggregate_metadata(
    component_ways: list[dict[str, Any]],
) -> dict[str, Any]:

    names: list[str] = []
    surfaces: list[str] = []
    difficulties: list[str] = []
    visibilities: list[str] = []
    operators: list[str] = []
    networks: list[str] = []
    highways: list[str] = []

    for way in component_ways:

        tags = way.get("tags") or {}

        if meaningful_name(
            tags.get("name")
        ):
            names.append(
                str(tags["name"])
            )

        if tags.get("surface"):
            surfaces.append(
                str(tags["surface"])
            )

        if tags.get("sac_scale"):
            difficulties.append(
                str(tags["sac_scale"])
            )

        if tags.get("trail_visibility"):
            visibilities.append(
                str(tags["trail_visibility"])
            )

        if tags.get("operator"):
            operators.append(
                str(tags["operator"])
            )

        if tags.get("network"):
            networks.append(
                str(tags["network"])
            )

        if tags.get("highway"):
            highways.append(
                str(tags["highway"])
            )

    return {
        "name": most_common_value(names),
        "surface": most_common_value(surfaces),
        "difficulty": most_common_value(difficulties),
        "trail_visibility": most_common_value(visibilities),
        "operator": most_common_value(operators),
        "network": most_common_value(networks),
        "highway": most_common_value(highways),
    }


# ============================================================
# NAMED TRAIL GROUPS
# ============================================================

def build_named_trail_groups(
    ways: list[dict[str, Any]],
    way_lookup: dict[int, dict[str, Any]],
    latitude: float,
    longitude: float,
    hiking_relation_way_ids: set[int],
    foot_relation_way_ids: set[int],
    search_query: str,
    location_name: str,
    broad_search: bool,
) -> list[dict[str, Any]]:

    named_ways = [
        way
        for way in ways
        if meaningful_name(
            (way.get("tags") or {}).get("name")
        )
        and is_way_strong_enough(
            way,
            hiking_relation_way_ids,
            foot_relation_way_ids,
        )
    ]

    by_name: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)

    for way in named_ways:

        name = str(
            (way.get("tags") or {}).get("name") or ""
        ).strip()

        if not name:
            continue

        by_name[
            normalize_name(name)
        ].append(way)

    candidates: list[dict[str, Any]] = []

    for same_named_ways in by_name.values():

        if len(same_named_ways) < 2:
            continue

        graph = build_way_graph(
            same_named_ways
        )

        components = find_connected_components(
            graph
        )

        for component_ids in components:

            if len(component_ids) < 2:
                continue

            if not component_is_simple_chain(
                component_ids,
                graph,
            ):
                continue

            component_ways = [
                way_lookup[way_id]
                for way_id in component_ids
                if way_id in way_lookup
            ]

            if len(component_ways) < 2:
                continue

            total_length = sum(
                get_way_length_km(way)
                for way in component_ways
            )

            if (
                total_length < 0.60
                or total_length > 25.0
            ):
                continue

            distance_km = min(
                distance_to_search_km(
                    way_coordinates(way),
                    latitude,
                    longitude,
                )
                for way in component_ways
            )

            evidence_scores: list[int] = []
            reasons: list[str] = []

            for way in component_ways:

                evidence_score, way_reasons = (
                    way_hiking_evidence(
                        way,
                        hiking_relation_way_ids,
                        foot_relation_way_ids,
                    )
                )

                evidence_scores.append(
                    evidence_score
                )

                reasons.extend(
                    way_reasons
                )

            strongest_evidence = max(
                evidence_scores,
                default=0,
            )

            metadata = aggregate_metadata(
                component_ways
            )

            name = metadata["name"]

            if not name:
                continue

            search_score, search_reasons = (
                text_relevance_score(
                    name,
                    search_query,
                    location_name,
                )
            )

            score = 90.0

            if trailish_name(name):
                score += 20

            if strongest_evidence >= 60:
                score += 25

            elif strongest_evidence >= 45:
                score += 15

            if metadata["difficulty"]:
                score += 8

            if metadata["trail_visibility"]:
                score += 6

            if metadata["network"]:
                score += 6

            score += min(
                60.0,
                float(search_score),
            )

            if broad_search:

                score += max(
                    0.0,
                    15.0 - distance_km * 0.50,
                )

            else:

                score += max(
                    0.0,
                    35.0 - distance_km * 5.0,
                )

            candidates.append(
                {
                    "osm_id": int(component_ids[0]),
                    "osm_type": "component",
                    "name": name,
                    "candidate_type": "named_trail_group",
                    "priority_tier": 2,
                    "route_type": None,
                    "highway_type": metadata["highway"],
                    "description": None,
                    "operator": metadata["operator"],
                    "network": metadata["network"],
                    "difficulty": metadata["difficulty"],
                    "surface": metadata["surface"],
                    "trail_visibility": (
                        metadata["trail_visibility"]
                    ),
                    "length_km": round(
                        total_length,
                        2,
                    ),
                    "distance_from_search_km": round(
                        distance_km,
                        2,
                    ),
                    "relevance_score": round(
                        score,
                        2,
                    ),
                    "hiking_evidence": min(
                        100,
                        strongest_evidence,
                    ),
                    "evidence_reasons": list(
                        dict.fromkeys(
                            reasons
                            + search_reasons
                            + [
                                "same-name connected trail segments"
                            ]
                        )
                    )[:8],
                    "member_way_ids": [
                        int(way_id)
                        for way_id in component_ids
                    ],
                }
            )

    return candidates


# ============================================================
# STRONG UNNAMED TRAIL GROUPS
# ============================================================

def is_strong_hiking_group_way(
    way: dict[str, Any],
    hiking_relation_way_ids: set[int],
    foot_relation_way_ids: set[int],
) -> bool:

    if not is_way_base_eligible(way):
        return False

    way_id = int(
        way.get("id") or 0
    )

    evidence_score, _ = way_hiking_evidence(
        way,
        hiking_relation_way_ids,
        foot_relation_way_ids,
    )

    tags = way.get("tags") or {}

    name = tags.get("name")

    # Explicit hiking relation
    if way_id in hiking_relation_way_ids:
        return True

    # Strong metadata evidence
    if evidence_score >= 40:
        return True

    # Named trail-like feature
    if (
        trailish_name(name)
        and evidence_score >= 25
    ):
        return True

    return False


def build_unnamed_strong_trail_groups(
    ways: list[dict[str, Any]],
    way_lookup: dict[int, dict[str, Any]],
    latitude: float,
    longitude: float,
    hiking_relation_way_ids: set[int],
    foot_relation_way_ids: set[int],
    search_query: str,
    location_name: str,
    broad_search: bool,
) -> list[dict[str, Any]]:

    strong_unnamed_ways = [
        way
        for way in ways
        if not meaningful_name(
            (way.get("tags") or {}).get("name")
        )
        and is_strong_hiking_group_way(
            way,
            hiking_relation_way_ids,
            foot_relation_way_ids,
        )
    ]

    if len(strong_unnamed_ways) < 2:
        return []

    graph = build_way_graph(
        strong_unnamed_ways
    )

    components = find_connected_components(
        graph
    )

    candidates: list[dict[str, Any]] = []

    for component_ids in components:

        if len(component_ids) < 2:
            continue

        # Safety:
        # only assemble simple chains.
        # Branching networks remain individual candidates.
        if not component_is_simple_chain(
            component_ids,
            graph,
        ):
            continue

        component_ways = [
            way_lookup[way_id]
            for way_id in component_ids
            if way_id in way_lookup
        ]

        if len(component_ways) < 2:
            continue

        total_length = sum(
            get_way_length_km(way)
            for way in component_ways
        )

        if (
            total_length < 0.60
            or total_length > 25.0
        ):
            continue

        distance_km = min(
            distance_to_search_km(
                way_coordinates(way),
                latitude,
                longitude,
            )
            for way in component_ways
        )

        evidence_scores: list[int] = []
        reasons: list[str] = []

        for way in component_ways:

            evidence_score, way_reasons = (
                way_hiking_evidence(
                    way,
                    hiking_relation_way_ids,
                    foot_relation_way_ids,
                )
            )

            evidence_scores.append(
                evidence_score
            )

            reasons.extend(
                way_reasons
            )

        strongest_evidence = max(
            evidence_scores,
            default=0,
        )

        average_evidence = (
            sum(evidence_scores)
            / len(evidence_scores)
        )

        # Prevent weak random path networks
        if average_evidence < 35:
            continue

        metadata = aggregate_metadata(
            component_ways
        )

        score = 92.0

        if strongest_evidence >= 60:
            score += 25

        elif strongest_evidence >= 45:
            score += 18

        else:
            score += 10

        if average_evidence >= 50:
            score += 10

        if metadata["difficulty"]:
            score += 8

        if metadata["trail_visibility"]:
            score += 5

        if total_length >= 1:
            score += 5

        if total_length >= 3:
            score += 8

        if broad_search:

            score += max(
                0.0,
                15.0 - distance_km * 0.50,
            )

        else:

            score += max(
                0.0,
                35.0 - distance_km * 5.0,
            )

        candidates.append(
            {
                "osm_id": int(component_ids[0]),
                "osm_type": "component",
                "name": (
                    metadata["name"]
                    or "Hiking trail"
                ),
                "candidate_type": (
                    "hiking_trail_group"
                ),
                "priority_tier": 2,
                "route_type": None,
                "highway_type": metadata["highway"],
                "description": None,
                "operator": metadata["operator"],
                "network": metadata["network"],
                "difficulty": metadata["difficulty"],
                "surface": metadata["surface"],
                "trail_visibility": (
                    metadata["trail_visibility"]
                ),
                "length_km": round(
                    total_length,
                    2,
                ),
                "distance_from_search_km": round(
                    distance_km,
                    2,
                ),
                "relevance_score": round(
                    score,
                    2,
                ),
                "hiking_evidence": min(
                    100,
                    int(round(average_evidence)),
                ),
                "evidence_reasons": list(
                    dict.fromkeys(
                        reasons
                        + [
                            "connected strong-hiking segments"
                        ]
                    )
                )[:8],
                "member_way_ids": [
                    int(way_id)
                    for way_id in component_ids
                ],
            }
        )

    return candidates


# ============================================================
# WAY SCORING
# ============================================================

def candidate_way_score(
    way: dict[str, Any],
    latitude: float,
    longitude: float,
    hiking_relation_way_ids: set[int],
    foot_relation_way_ids: set[int],
    search_query: str,
    location_name: str,
    broad_search: bool,
) -> tuple[
    float,
    int,
    int,
    list[str],
]:

    tags = way.get("tags") or {}

    highway = tags.get("highway")
    name = tags.get("name")

    length_km = get_way_length_km(
        way
    )

    distance_km = distance_to_search_km(
        way_coordinates(way),
        latitude,
        longitude,
    )

    evidence_score, evidence_reasons = (
        way_hiking_evidence(
            way,
            hiking_relation_way_ids,
            foot_relation_way_ids,
        )
    )

    search_score, search_reasons = (
        text_relevance_score(
            name,
            search_query,
            location_name,
        )
    )

    evidence_reasons = list(
        dict.fromkeys(
            evidence_reasons
            + search_reasons
        )
    )

    way_id = int(
        way.get("id") or 0
    )

    if way_id in hiking_relation_way_ids:

        priority_tier = 1
        score = 120.0

    elif evidence_score >= 45:

        priority_tier = 2
        score = 92.0

    elif trailish_name(name):

        priority_tier = 2
        score = 82.0

    else:

        priority_tier = 3
        score = 60.0

    score += min(
        60.0,
        float(search_score),
    )

    if meaningful_name(name):
        score += 12

    if trailish_name(name):
        score += 20

    if tags.get("sac_scale"):
        score += 16

    if tags.get("trail_visibility"):
        score += 8

    if tags.get("network"):
        score += 6

    if tags.get("operator"):
        score += 4

    if length_km >= 0.75:
        score += 5

    if length_km >= 1.5:
        score += 8

    if length_km >= 3.0:
        score += 8

    if broad_search:

        score += max(
            0.0,
            15.0 - distance_km * 0.50,
        )

    else:

        score += max(
            0.0,
            35.0 - distance_km * 5.0,
        )

    if (
        road_like_name(name)
        and not trailish_name(name)
    ):
        score -= 70

    if (
        highway == "track"
        and normalize_name(
            tags.get("surface")
        ) in PAVED_SURFACES
    ):
        score -= 70

    return (
        round(score, 2),
        priority_tier,
        evidence_score,
        evidence_reasons,
    )


# ============================================================
# BUILD WAY CANDIDATE
# ============================================================

def build_way_candidate(
    way: dict[str, Any],
    latitude: float,
    longitude: float,
    hiking_relation_way_ids: set[int],
    foot_relation_way_ids: set[int],
    search_query: str,
    location_name: str,
    broad_search: bool,
) -> dict[str, Any] | None:

    tags = way.get("tags") or {}

    if not is_way_strong_enough(
        way,
        hiking_relation_way_ids,
        foot_relation_way_ids,
    ):
        return None

    coordinates = way_coordinates(way)

    length_km = coordinate_distance_km(
        coordinates
    )

    if length_km <= 0:
        return None

    (
        score,
        priority_tier,
        evidence_score,
        evidence_reasons,
    ) = candidate_way_score(
        way,
        latitude,
        longitude,
        hiking_relation_way_ids,
        foot_relation_way_ids,
        search_query,
        location_name,
        broad_search,
    )

    name = (
        str(tags.get("name")).strip()
        if tags.get("name")
        else None
    )

    if name and (
        normalize_name(name)
        in GENERIC_NAMES
        or (
            road_like_name(name)
            and not trailish_name(name)
        )
    ):
        name = None

    highway = tags.get("highway")

    if highway == "footway":

        candidate_type = "footway_trail"
        fallback_name = "Hiking footway"

    elif highway == "track":

        candidate_type = "track_trail"
        fallback_name = "Trail track"

    elif highway == "steps":

        candidate_type = "hiking_path"
        fallback_name = "Hiking trail steps"

    else:

        candidate_type = (
            "hiking_path"
            if priority_tier <= 2
            else "path_segment"
        )

        fallback_name = (
            "Hiking path"
            if priority_tier <= 2
            else "Outdoor path"
        )

    return {
        "osm_id": int(way["id"]),
        "osm_type": "way",
        "name": name or fallback_name,
        "candidate_type": candidate_type,
        "priority_tier": priority_tier,
        "route_type": None,
        "highway_type": highway,
        "description": tags.get("description"),
        "operator": tags.get("operator"),
        "network": tags.get("network"),
        "difficulty": tags.get("sac_scale"),
        "surface": tags.get("surface"),
        "trail_visibility": (
            tags.get("trail_visibility")
        ),
        "length_km": round(
            length_km,
            2,
        ),
        "distance_from_search_km": round(
            distance_to_search_km(
                coordinates,
                latitude,
                longitude,
            ),
            2,
        ),
        "relevance_score": score,
        "hiking_evidence": min(
            100,
            max(0, evidence_score),
        ),
        "evidence_reasons": list(
            dict.fromkeys(
                evidence_reasons
            )
        )[:8],
    }


# ============================================================
# RELATION CANDIDATES
# ============================================================

def build_relation_candidate(
    relation: dict[str, Any],
    way_lookup: dict[int, dict[str, Any]],
    latitude: float,
    longitude: float,
    search_query: str,
    location_name: str,
    broad_search: bool,
) -> dict[str, Any] | None:

    tags = relation.get("tags") or {}

    route = tags.get("route")

    if route not in {
        "hiking",
        "foot",
    }:
        return None

    member_way_ids = relation_member_ids(
        relation
    )

    member_ways = [
        way_lookup[way_id]
        for way_id in member_way_ids
        if way_id in way_lookup
        and is_way_base_eligible(
            way_lookup[way_id]
        )
    ]

    if not member_ways:
        return None

    unique_member_ids = list(
        dict.fromkeys(
            int(way["id"])
            for way in member_ways
        )
    )

    length_km = sum(
        get_way_length_km(way)
        for way in member_ways
    )

    if length_km <= 0:
        return None

    distance_km = min(
        distance_to_search_km(
            way_coordinates(way),
            latitude,
            longitude,
        )
        for way in member_ways
    )

    name = tags.get("name")

    if (
        name
        and road_like_name(name)
        and not trailish_name(name)
    ):
        name = None

    search_score, search_reasons = (
        text_relevance_score(
            name,
            search_query,
            location_name,
        )
    )

    if route == "hiking":

        priority_tier = 1
        score = 180.0
        candidate_type = "hiking_route"

    else:

        priority_tier = 2
        score = 135.0
        candidate_type = "walking_route"

    score += min(
        70.0,
        float(search_score),
    )

    if meaningful_name(name):
        score += 20

    if trailish_name(name):
        score += 18

    if tags.get("network"):
        score += 10

    if tags.get("operator"):
        score += 6

    if tags.get("description"):
        score += 4

    if length_km >= 1:
        score += 8

    if length_km >= 3:
        score += 10

    if length_km >= 5:
        score += 6

    if broad_search:

        score += max(
            0.0,
            15.0 - distance_km * 0.50,
        )

    else:

        score += max(
            0.0,
            35.0 - distance_km * 5.0,
        )

    return {
        "osm_id": int(relation["id"]),
        "osm_type": "relation",
        "name": name or "Hiking route",
        "candidate_type": candidate_type,
        "priority_tier": priority_tier,
        "route_type": route,
        "highway_type": None,
        "description": tags.get("description"),
        "operator": tags.get("operator"),
        "network": tags.get("network"),
        "difficulty": tags.get("sac_scale"),
        "surface": None,
        "trail_visibility": None,
        "length_km": round(
            length_km,
            2,
        ),
        "distance_from_search_km": round(
            distance_km,
            2,
        ),
        "relevance_score": round(
            score,
            2,
        ),
        "hiking_evidence": (
            100
            if route == "hiking"
            else 70
        ),
        "evidence_reasons": list(
            dict.fromkeys(
                [
                    (
                        "hiking route relation"
                        if route == "hiking"
                        else "walking route relation"
                    )
                ]
                + search_reasons
            )
        )[:8],
        "member_way_ids": unique_member_ids,
    }


# ============================================================
# DUPLICATE SUPPRESSION
# ============================================================

def member_overlap(
    first: dict[str, Any],
    second: dict[str, Any],
) -> float:

    ids_a = set(
        first.get("member_way_ids") or []
    )

    ids_b = set(
        second.get("member_way_ids") or []
    )

    if not ids_a or not ids_b:
        return 0.0

    return (
        len(ids_a & ids_b)
        / min(
            len(ids_a),
            len(ids_b),
        )
    )


def suppress_duplicate_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    ranked = sorted(
        candidates,
        key=lambda candidate: (
            candidate["priority_tier"],
            -candidate["relevance_score"],
            candidate.get(
                "distance_from_search_km",
                float("inf"),
            ),
            -candidate["length_km"],
        ),
    )

    kept: list[dict[str, Any]] = []

    seen_object_ids: set[
        tuple[str, int]
    ] = set()

    for candidate in ranked:

        object_key = (
            str(candidate["osm_type"]),
            int(candidate["osm_id"]),
        )

        if object_key in seen_object_ids:
            continue

        seen_object_ids.add(object_key)

        duplicate = False

        for existing in kept:

            # Exact member overlap for grouped structures
            if (
                candidate["osm_type"]
                in {"relation", "component"}
                and existing["osm_type"]
                in {"relation", "component"}
            ):

                overlap = member_overlap(
                    candidate,
                    existing,
                )

                if overlap >= 0.80:

                    duplicate = True
                    break

            # Prevent standalone way duplicate
            candidate_way_id = (
                candidate["osm_id"]
                if candidate["osm_type"] == "way"
                else None
            )

            existing_members = set(
                existing.get(
                    "member_way_ids"
                ) or []
            )

            if (
                candidate_way_id
                and candidate_way_id
                in existing_members
            ):

                duplicate = True
                break

        if duplicate:
            continue

        kept.append(candidate)

    return kept


# ============================================================
# FINAL CANDIDATE SELECTION
# ============================================================

def select_final_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    ranked = sorted(
        candidates,
        key=lambda candidate: (
            candidate["priority_tier"],
            -candidate["relevance_score"],
            candidate.get(
                "distance_from_search_km",
                float("inf"),
            ),
            -candidate["length_km"],
        ),
    )

    strong_candidates = [
        candidate
        for candidate in ranked
        if candidate["priority_tier"] <= 2
    ]

    # --------------------------------------------------------
    # Primary result set
    # --------------------------------------------------------

    if strong_candidates:

        return strong_candidates[:10]

    # --------------------------------------------------------
    # Meaningful fallback
    # --------------------------------------------------------

    meaningful_fallbacks = [
        candidate
        for candidate in ranked
        if (
            candidate["priority_tier"] == 3
            and (
                meaningful_name(
                    candidate.get("name")
                )
                or candidate.get(
                    "hiking_evidence",
                    0,
                ) >= 20
            )
        )
    ]

    if meaningful_fallbacks:
        return meaningful_fallbacks[:6]

    # --------------------------------------------------------
    # Sparse fallback
    # --------------------------------------------------------

    sparse_fallbacks = [
        candidate
        for candidate in ranked
        if (
            candidate.get("highway_type")
            in {
                "path",
                "footway",
                "track",
            }
            and candidate.get(
                "length_km",
                0,
            ) >= 0.75
        )
    ]

    return sparse_fallbacks[:3]


# ============================================================
# OVERPASS
# ============================================================

async def run_overpass(
    query: str,
) -> dict[str, Any]:

    last_error: Exception | None = None

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=10.0,
            read=90.0,
            write=20.0,
            pool=10.0,
        ),
        headers=HEADERS,
    ) as client:

        for url in OVERPASS_URLS:

            try:

                response = await client.post(
                    url,
                    data=query,
                )

                response.raise_for_status()

                return response.json()

            except Exception as exc:

                last_error = exc

    raise HTTPException(
        status_code=502,
        detail=(
            "Overpass request failed: "
            f"{last_error}"
        ),
    )


# ============================================================
# MAP TRAIL PREVIEW
# ============================================================

def build_map_trails(
    candidates: list[dict[str, Any]],
    way_lookup: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:

    map_trails: list[dict[str, Any]] = []

    for candidate in candidates:

        osm_type = candidate.get("osm_type")

        # ----------------------------------------------------
        # Standalone way
        # ----------------------------------------------------

        if osm_type == "way":

            way = way_lookup.get(
                int(candidate["osm_id"])
            )

            if not way:
                continue

            coordinates = way_coordinates(way)

            if len(coordinates) < 2:
                continue

            map_trails.append(
                {
                    "osm_id": candidate["osm_id"],
                    "osm_type": osm_type,
                    "name": candidate["name"],
                    "geometry_type": "LineString",
                    "coordinates": coordinates,
                }
            )

        # ----------------------------------------------------
        # Components
        # ----------------------------------------------------

        elif osm_type == "component":

            segments: list[
                list[list[float]]
            ] = []

            for way_id in (
                candidate.get(
                    "member_way_ids"
                )
                or []
            ):

                way = way_lookup.get(
                    int(way_id)
                )

                if not way:
                    continue

                coordinates = way_coordinates(way)

                if len(coordinates) >= 2:
                    segments.append(
                        coordinates
                    )

            if not segments:
                continue

            map_trails.append(
                {
                    "osm_id": candidate["osm_id"],
                    "osm_type": osm_type,
                    "name": candidate["name"],
                    "geometry_type": (
                        "LineString"
                        if len(segments) == 1
                        else "MultiLineString"
                    ),
                    "coordinates": (
                        segments[0]
                        if len(segments) == 1
                        else segments
                    ),
                }
            )

    return map_trails


# ============================================================
# TRAIL DISCOVERY ENDPOINT
# ============================================================

@router.get("/trails")
async def discover_trails(

    latitude: float = Query(...),

    longitude: float = Query(...),

    radius_m: int = Query(
        5000,
        ge=1000,
        le=30000,
    ),

    scope: str = Query(
        "local",
        pattern="^(local|area)$",
    ),

    search_query: str = Query(
        "",
        max_length=200,
    ),

    location_name: str = Query(
        "",
        max_length=300,
    ),
):

    broad_search = scope == "area"

    # --------------------------------------------------------
    # Overpass query
    # --------------------------------------------------------

    query = f"""
    [out:json][timeout:90];

    (
      relation["route"="hiking"]
        (around:{radius_m},{latitude},{longitude});

      relation["route"="foot"]
        (around:{radius_m},{latitude},{longitude});

      way["highway"="path"]
        (around:{radius_m},{latitude},{longitude});

      way["highway"="footway"]
        (around:{radius_m},{latitude},{longitude});

      way["highway"="track"]
        (around:{radius_m},{latitude},{longitude});

      way["highway"="steps"]
        (around:{radius_m},{latitude},{longitude});
    );

    out body geom;
    """

    data = await run_overpass(query)

    elements = (
        data.get("elements") or []
    )

    relations: list[
        dict[str, Any]
    ] = []

    raw_ways: list[
        dict[str, Any]
    ] = []

    for element in elements:

        element_type = element.get("type")
        element_id = element.get("id")

        if not element_id:
            continue

        if element_type == "relation":

            route = (
                element.get("tags") or {}
            ).get("route")

            if route in {
                "hiking",
                "foot",
            }:

                relations.append(element)

        elif element_type == "way":

            raw_ways.append(element)

    # --------------------------------------------------------
    # Way lookup
    # --------------------------------------------------------

    way_lookup = {
        int(way["id"]): way
        for way in raw_ways
        if way.get("id")
    }

    # --------------------------------------------------------
    # Relation member sets
    # --------------------------------------------------------

    hiking_relation_way_ids: set[int] = set()

    foot_relation_way_ids: set[int] = set()

    for relation in relations:

        route = (
            relation.get("tags") or {}
        ).get("route")

        target_set = (
            hiking_relation_way_ids
            if route == "hiking"
            else foot_relation_way_ids
        )

        target_set.update(
            way_id
            for way_id in relation_member_ids(
                relation
            )
            if way_id in way_lookup
        )

    # --------------------------------------------------------
    # Base eligible ways
    # --------------------------------------------------------

    base_eligible_ways = [
        way
        for way in raw_ways
        if is_way_base_eligible(way)
    ]

    # --------------------------------------------------------
    # Strong way diagnostic
    # --------------------------------------------------------

    strong_current_ways = [
        way
        for way in base_eligible_ways
        if is_way_strong_enough(
            way,
            hiking_relation_way_ids,
            foot_relation_way_ids,
        )
    ]

    # ========================================================
    # BUILD CANDIDATE TYPES
    # ========================================================

    # --------------------------------------------------------
    # 1. Official route relations
    # --------------------------------------------------------

    relation_candidates = [

        candidate

        for relation in relations

        for candidate in [

            build_relation_candidate(
                relation,
                way_lookup,
                latitude,
                longitude,
                search_query,
                location_name,
                broad_search,
            )

        ]

        if candidate is not None
    ]

    # --------------------------------------------------------
    # 2. Named trail groups
    # --------------------------------------------------------

    named_group_candidates = (
        build_named_trail_groups(
            base_eligible_ways,
            way_lookup,
            latitude,
            longitude,
            hiking_relation_way_ids,
            foot_relation_way_ids,
            search_query,
            location_name,
            broad_search,
        )
    )

    # --------------------------------------------------------
    # 3. Unnamed strong hiking groups
    #
    # IMPORTANT:
    # This was missing from your previous pipeline.
    # --------------------------------------------------------

    unnamed_group_candidates = (
        build_unnamed_strong_trail_groups(
            base_eligible_ways,
            way_lookup,
            latitude,
            longitude,
            hiking_relation_way_ids,
            foot_relation_way_ids,
            search_query,
            location_name,
            broad_search,
        )
    )

    # --------------------------------------------------------
    # Coverage tracking
    #
    # Only grouped COMPONENTS hide their exact member ways.
    #
    # Relations do NOT automatically hide all their members.
    # This prevents one relation from collapsing the result
    # set to a single trail.
    # --------------------------------------------------------

    covered_way_ids = {
        int(way_id)

        for candidate in (
            named_group_candidates
            + unnamed_group_candidates
        )

        for way_id in (
            candidate.get("member_way_ids")
            or []
        )
    }

    # --------------------------------------------------------
    # 4. Standalone ways
    # --------------------------------------------------------

    standalone_before_coverage = 0

    way_candidates: list[
        dict[str, Any]
    ] = []

    for way in base_eligible_ways:

        possible_candidate = build_way_candidate(
            way,
            latitude,
            longitude,
            hiking_relation_way_ids,
            foot_relation_way_ids,
            search_query,
            location_name,
            broad_search,
        )

        if possible_candidate:
            standalone_before_coverage += 1

        way_id = int(
            way.get("id") or 0
        )

        if way_id in covered_way_ids:
            continue

        if possible_candidate:
            way_candidates.append(
                possible_candidate
            )

    # ========================================================
    # COMBINE
    # ========================================================

    all_candidates = (
        relation_candidates
        + named_group_candidates
        + unnamed_group_candidates
        + way_candidates
    )

    # ========================================================
    # DEDUPLICATION
    # ========================================================

    deduplicated_candidates = (
        suppress_duplicate_candidates(
            all_candidates
        )
    )

    # ========================================================
    # FINAL SELECTION
    # ========================================================

    final_candidates = (
        select_final_candidates(
            deduplicated_candidates
        )
    )

    # ========================================================
    # MAP DATA
    # ========================================================

    map_trails = build_map_trails(
        final_candidates,
        way_lookup,
    )

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    highway_distribution = Counter(
        (
            way.get("tags") or {}
        ).get(
            "highway",
            "unknown",
        )
        for way in raw_ways
    )

    surface_distribution = Counter(
        normalize_name(
            (way.get("tags") or {}).get(
                "surface"
            )
        )
        or "unknown"
        for way in raw_ways
    )

    named_ways = sum(
        1
        for way in raw_ways
        if meaningful_name(
            (way.get("tags") or {}).get(
                "name"
            )
        )
    )

    road_like_names = sum(
        1
        for way in raw_ways
        if road_like_name(
            (way.get("tags") or {}).get(
                "name"
            )
        )
    )

    trail_like_names = sum(
        1
        for way in raw_ways
        if trailish_name(
            (way.get("tags") or {}).get(
                "name"
            )
        )
    )

    ways_with_sac_scale = sum(
        1
        for way in raw_ways
        if (
            way.get("tags") or {}
        ).get("sac_scale")
    )

    ways_with_trail_visibility = sum(
        1
        for way in raw_ways
        if (
            way.get("tags") or {}
        ).get("trail_visibility")
    )

    ways_with_designated_foot = sum(
        1
        for way in raw_ways
        if (
            way.get("tags") or {}
        ).get("foot")
        in {
            "designated",
            "yes",
        }
    )

    strong_evidence_ways = sum(
        1
        for way in base_eligible_ways
        if way_hiking_evidence(
            way,
            hiking_relation_way_ids,
            foot_relation_way_ids,
        )[0] >= 45
    )

    unpaved_ways = sum(
        1
        for way in raw_ways
        if normalize_name(
            (way.get("tags") or {}).get(
                "surface"
            )
        )
        in UNPAVED_SURFACES
    )

    paved_ways = sum(
        1
        for way in raw_ways
        if normalize_name(
            (way.get("tags") or {}).get(
                "surface"
            )
        )
        in PAVED_SURFACES
    )

    # ========================================================
    # RESPONSE
    # ========================================================

    return {

        "source": "OpenStreetMap",

        "search": {
            "latitude": latitude,
            "longitude": longitude,
            "radius_m": radius_m,
            "scope": scope,
            "search_query": search_query,
            "location_name": location_name,
        },

        "count": len(final_candidates),

        "trails": final_candidates,

        "map_trails": map_trails,

        "pipeline": {

            "total_osm_elements": len(elements),

            "raw_ways": len(raw_ways),

            "relations": len(relations),

            "base_eligible_ways": len(
                base_eligible_ways
            ),

            "strong_current_ways": len(
                strong_current_ways
            ),

            "relation_candidates": len(
                relation_candidates
            ),

            "named_group_candidates": len(
                named_group_candidates
            ),

            "unnamed_group_candidates": len(
                unnamed_group_candidates
            ),

            "standalone_before_coverage": (
                standalone_before_coverage
            ),

            "standalone_after_coverage": len(
                way_candidates
            ),

            "all_candidates": len(
                all_candidates
            ),

            "after_duplicate_suppression": len(
                deduplicated_candidates
            ),

            "final_candidates": len(
                final_candidates
            ),
        },

        "diagnostics": {

            "totals": {

                "total_elements": len(elements),

                "total_ways": len(raw_ways),

                "total_relations": len(relations),
            },

            "relations": {

                "hiking_relations": sum(
                    1
                    for relation in relations
                    if (
                        relation.get("tags")
                        or {}
                    ).get("route")
                    == "hiking"
                ),

                "foot_relations": sum(
                    1
                    for relation in relations
                    if (
                        relation.get("tags")
                        or {}
                    ).get("route")
                    == "foot"
                ),
            },

            "ways": {

                "named_ways": named_ways,

                "road_like_names": road_like_names,

                "trail_like_names": trail_like_names,

                "base_eligible_ways": len(
                    base_eligible_ways
                ),

                "strong_current_candidates": len(
                    strong_current_ways
                ),

                "strong_evidence_ways": (
                    strong_evidence_ways
                ),

                "unpaved_ways": unpaved_ways,

                "paved_ways": paved_ways,

                "ways_with_sac_scale": (
                    ways_with_sac_scale
                ),

                "ways_with_trail_visibility": (
                    ways_with_trail_visibility
                ),

                "ways_with_designated_foot": (
                    ways_with_designated_foot
                ),
            },

            "highway_distribution": dict(
                highway_distribution
            ),

            "surface_distribution": dict(
                surface_distribution
            ),
        },
    }


# ============================================================
# FETCH FULL OSM OBJECT
# ============================================================

async def fetch_osm_full(
    osm_type: str,
    osm_id: int,
) -> dict[str, Any]:

    url = (
        "https://api.openstreetmap.org/api/0.6/"
        f"{osm_type}/{osm_id}/full.json"
    )

    try:

        async with httpx.AsyncClient(
            timeout=30.0,
            headers=HEADERS,
        ) as client:

            response = await client.get(url)

            if response.status_code == 404:

                raise HTTPException(
                    status_code=404,
                    detail="OSM object not found",
                )

            response.raise_for_status()

            return response.json()

    except HTTPException:
        raise

    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                "OpenStreetMap request failed: "
                f"{exc}"
            ),
        )


# ============================================================
# GEOMETRY EXTRACTION
# ============================================================

def extract_way_coordinates(
    way: dict[str, Any],
    nodes: dict[int, list[float]],
) -> list[list[float]]:

    coordinates: list[list[float]] = []

    for node_id in (
        way.get("nodes") or []
    ):

        try:

            coordinate = nodes.get(
                int(node_id)
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        if coordinate:

            coordinates.append(
                coordinate
            )

    return coordinates


def geometry_from_segments(
    segments: list[list[list[float]]],
) -> dict[str, Any]:

    if len(segments) == 1:

        return {
            "type": "LineString",
            "coordinates": segments[0],
        }

    return {
        "type": "MultiLineString",
        "coordinates": segments,
    }


# ============================================================
# COMPONENT GEOMETRY
# ============================================================

@router.get(
    "/trails/component/{component_id}"
)
async def get_component_geometry(

    component_id: int,

    member_way_ids: str = Query(...),

    trail_name: str | None = Query(
        default=None
    ),
):

    try:

        way_ids = [

            int(value.strip())

            for value in member_way_ids.split(",")

            if value.strip()
        ]

    except ValueError as exc:

        raise HTTPException(
            status_code=400,
            detail=(
                "member_way_ids must be "
                "comma-separated integers"
            ),
        ) from exc

    way_ids = list(
        dict.fromkeys(way_ids)
    )

    if not way_ids:

        raise HTTPException(
            status_code=400,
            detail=(
                "At least one member way "
                "is required"
            ),
        )

    id_list = ",".join(
        str(way_id)
        for way_id in way_ids
    )

    query = f"""
    [out:json][timeout:60];

    way(id:{id_list});

    out body geom;
    """

    data = await run_overpass(query)

    ways = [

        element

        for element in (
            data.get("elements") or []
        )

        if element.get("type") == "way"
    ]

    by_id = {

        int(way["id"]): way

        for way in ways

        if way.get("id")
    }

    coordinate_segments: list[
        list[list[float]]
    ] = []

    first_tags: dict[str, Any] = {}

    for way_id in way_ids:

        way = by_id.get(way_id)

        if not way:
            continue

        coordinates = way_coordinates(way)

        if len(coordinates) < 2:
            continue

        coordinate_segments.append(
            coordinates
        )

        if not first_tags:

            first_tags = (
                way.get("tags") or {}
            )

    if not coordinate_segments:

        raise HTTPException(
            status_code=404,
            detail=(
                "No usable component "
                "geometry found"
            ),
        )

    total_distance = sum(

        coordinate_distance_km(segment)

        for segment in coordinate_segments
    )

    return {

        "source": "OpenStreetMap",

        "osm_id": component_id,

        "osm_type": "component",

        "name": (
            trail_name
            or "Hiking trail"
        ),

        "route_type": None,

        "highway_type": first_tags.get(
            "highway",
            "path",
        ),

        "description": None,

        "difficulty": first_tags.get(
            "sac_scale"
        ),

        "surface": first_tags.get(
            "surface"
        ),

        "trail_visibility": first_tags.get(
            "trail_visibility"
        ),

        "distance_km": round(
            total_distance,
            2,
        ),

        "raw_distance_km": round(
            total_distance,
            2,
        ),

        "segment_count": len(
            coordinate_segments
        ),

        "ordered_segment_count": len(
            coordinate_segments
        ),

        "geometry": geometry_from_segments(
            coordinate_segments
        ),
    }


# ============================================================
# TRAIL GEOMETRY
# ============================================================

@router.get(
    "/trails/{osm_type}/{osm_id}"
)
async def get_trail_geometry(

    osm_type: str,

    osm_id: int,
):

    if osm_type not in {
        "way",
        "relation",
    }:

        raise HTTPException(
            status_code=400,
            detail=(
                "osm_type must be "
                "'way' or 'relation'"
            ),
        )

    data = await fetch_osm_full(
        osm_type,
        osm_id,
    )

    elements = (
        data.get("elements") or []
    )

    nodes: dict[
        int,
        list[float],
    ] = {}

    ways: dict[
        int,
        dict[str, Any],
    ] = {}

    target: dict[str, Any] | None = None

    for element in elements:

        element_type = element.get("type")

        element_id = element.get("id")

        if (
            element_type == "node"
            and element_id
        ):

            nodes[int(element_id)] = [
                float(element["lon"]),
                float(element["lat"]),
            ]

        elif (
            element_type == "way"
            and element_id
        ):

            ways[int(element_id)] = element

        if (
            element_type == osm_type
            and element_id == osm_id
        ):

            target = element

    if target is None:

        raise HTTPException(
            status_code=404,
            detail=(
                "Target OSM object "
                "not found"
            ),
        )

    target_tags = (
        target.get("tags") or {}
    )

    coordinate_segments: list[
        list[list[float]]
    ] = []

    # --------------------------------------------------------
    # WAY
    # --------------------------------------------------------

    if osm_type == "way":

        coordinates = extract_way_coordinates(
            target,
            nodes,
        )

        if len(coordinates) >= 2:

            coordinate_segments.append(
                coordinates
            )

    # --------------------------------------------------------
    # RELATION
    # --------------------------------------------------------

    else:

        seen_way_ids: set[int] = set()

        for member in (
            target.get("members") or []
        ):

            if member.get("type") != "way":
                continue

            try:

                way_id = int(
                    member.get("ref")
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

            if way_id in seen_way_ids:
                continue

            seen_way_ids.add(way_id)

            way = ways.get(way_id)

            if not way:
                continue

            coordinates = extract_way_coordinates(
                way,
                nodes,
            )

            if len(coordinates) >= 2:

                coordinate_segments.append(
                    coordinates
                )

    if not coordinate_segments:

        raise HTTPException(
            status_code=404,
            detail=(
                "No usable geometry found"
            ),
        )

    total_distance = sum(

        coordinate_distance_km(segment)

        for segment in coordinate_segments
    )

    return {

        "source": "OpenStreetMap",

        "osm_id": osm_id,

        "osm_type": osm_type,

        "name": target_tags.get("name"),

        "route_type": target_tags.get("route"),

        "highway_type": target_tags.get(
            "highway"
        ),

        "description": target_tags.get(
            "description"
        ),

        "difficulty": target_tags.get(
            "sac_scale"
        ),

        "surface": target_tags.get(
            "surface"
        ),

        "trail_visibility": target_tags.get(
            "trail_visibility"
        ),

        "distance_km": round(
            total_distance,
            2,
        ),

        "geometry": geometry_from_segments(
            coordinate_segments
        ),
    }


# ============================================================
# RAW FULL OSM OBJECT
# ============================================================

@router.get(
    "/full/{osm_type}/{osm_id}"
)
async def fetch_osm_object_full(

    osm_type: str,

    osm_id: int,
):

    if osm_type not in {
        "way",
        "relation",
    }:

        raise HTTPException(
            status_code=400,
            detail=(
                "osm_type must be "
                "'way' or 'relation'"
            ),
        )

    return await fetch_osm_full(
        osm_type,
        osm_id,
    )