/**
 * Identity-first trail grouping.
 *
 * ONE verified OSM trail identity is ONE primary user-facing trail card.
 *
 * Identity precedence (first available wins):
 *
 *   1. `trail_id` — backend-unique across every namespace the discovery
 *      route emits: `relation:<id>`, `way:<id>`, `component:<hash of member
 *      way ids>`, `discovered:<hash of name>`. The backend already dedupes
 *      on this value before responding, so it is unique per object.
 *   2. `osm_type` + `osm_id` — the authoritative OSM identity, used only if
 *      `trail_id` is ever absent.
 *   3. No identity at all — never merged. The object gets a unique
 *      per-position key so it always stands alone.
 *
 * What this means in practice:
 *
 *   - Two relations with the same name stay two cards. Two ways with the
 *     same name stay two cards. Name and proximity NEVER merge distinct
 *     authoritative identities.
 *   - A connected component (`osm_type: "component"`, `osm_id: null`) is
 *     one card by construction: its stable id already covers all of its
 *     member ways, and no OSM identity is invented for it.
 *   - UNMAPPED candidates (`osm_type: null`) live in the `discovered:`
 *     namespace, which can never collide with a MAP_READY identity, so an
 *     unmapped candidate is never folded into a mapped trail.
 *   - Same-named member ways of an accepted route never reach this layer
 *     as separate objects: the backend folds them into the route card by
 *     OSM membership plus name agreement. What arrives here as distinct
 *     objects is distinct trails and stays distinct.
 */
export type TrailIdentity = {
  trail_id: string;
  osm_id: number | null;
  osm_type: "way" | "relation" | "component" | null;
};

export type TrailGroup<T extends TrailIdentity> = {
  key: string;
  label: string;
  primary: T;
};

export function trailIdentityKey(
  trail: Pick<TrailIdentity, "trail_id" | "osm_type" | "osm_id">,
): string | null {
  const trailId = (trail.trail_id ?? "").trim();
  if (trailId) {
    return `id:${trailId}`;
  }

  if (
    trail.osm_type !== null &&
    trail.osm_type !== undefined &&
    trail.osm_id !== null &&
    trail.osm_id !== undefined
  ) {
    return `${trail.osm_type}:${trail.osm_id}`;
  }

  return null;
}

export function buildTrailGroups<T extends TrailIdentity>(
  trails: T[],
  labelOf: (trail: T) => string,
): Array<TrailGroup<T>> {
  return trails.map((trail, index) => {
    const identity = trailIdentityKey(trail) ?? `unidentified:${index}`;
    return {
      key: identity,
      label: labelOf(trail),
      primary: trail,
    };
  });
}
