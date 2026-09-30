import { API_BASE_URL } from "./helpers";
import type {
  ProductSearchResponse,
  TrailGeometry,
  TrailIntelligenceResponse,
} from "./types";

/**
 * POST a JSON body to the backend and parse the JSON reply.
 *
 * The request is never cached and is bound to `signal`, so a superseded or
 * timed-out request aborts. A non-2xx status throws with `failureLabel`, and
 * callers keep their own handling of abort, staleness and user-facing wording.
 */
export async function postJson<T>(
  path: string,
  body: unknown,
  signal: AbortSignal,
  failureLabel: string,
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    cache: "no-store",
    signal,
  });

  if (!response.ok) {
    throw new Error(`${failureLabel} failed: ${response.status}`);
  }

  return (await response.json()) as T;
}

/** What product search needs to know about the trail. */
export function buildProductsRequest(
  intelligence: TrailIntelligenceResponse,
) {
  return {
    intelligence: {
      gear: intelligence.gear,
      condition: intelligence.condition,
    },
  };
}

/**
 * What the assistant is given: the trail's identity and the same verified
 * intelligence the page shows, including the shop cards already loaded so a
 * question such as "where can I buy poles?" can be answered.
 */
export function buildAssistantRequest({
  question,
  trail,
  intelligence,
  products,
}: {
  question: string;
  trail: TrailGeometry;
  intelligence: TrailIntelligenceResponse;
  products: ProductSearchResponse | null;
}) {
  return {
    question: question.trim(),
    trail: {
      trail_id: trail.trail_id,
      osm_type: trail.osm_type,
      osm_id: trail.osm_id,
      name: trail.name,
      route_type: trail.route_type,
      highway_type: trail.highway_type,
      source: trail.source,
      geometry_hash: trail.geometry_hash,
      member_way_ids: trail.member_way_ids,
    },
    intelligence: {
      analysis: {
        distance_km: intelligence.analysis.distance_km,
        component_count: intelligence.analysis.component_count,
      },
      terrain: intelligence.terrain
        ? { metrics: intelligence.terrain.metrics }
        : null,
      weather: intelligence.weather,
      condition: intelligence.condition,
      suitability: intelligence.suitability,
      gear: intelligence.gear,
      difficulty: intelligence.difficulty,
      products: products
        ? {
            groups: products.groups.map((group) => ({
              item: group.item,
              status: group.status,
              card: group.card,
            })),
          }
        : undefined,
    },
  };
}
