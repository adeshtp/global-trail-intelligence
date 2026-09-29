"""
Geographic grouping for the difficulty dataset.

The split that matters in this project is a SPATIAL one: a way must not be
able to appear in training and in the test set just because two parts of the
same route fall either side of a grid line. Reporting "group overlap = 0" is
not enough on its own, because the group has to be defined so that it actually
contains a whole route, not two halves of one.

HOW A GROUP IS BUILT

1. Every way starts in the one-degree cell that contains its centroid. That is
   the geographic unit the dataset builder already sampled on, so the grouping
   is the same partition the data collection used.

2. Ways that are physically connected are then merged. Two ways are connected
   when they share an endpoint, which is what consecutive members of one OSM
   route look like. Connected ways are unioned regardless of which cells they
   fall in, so a route that crosses a cell boundary becomes a single group and
   can no longer be split across the train/test boundary.

The result is a strictly coarser partition than the cell grid, so it can only
reduce leakage, never introduce it. `describe` reports what the merge actually
changed, so the effect is measured rather than asserted.

WHAT THIS CANNOT DO

Postpass's way table does not expose relation membership, so two ways that
belong to the same named route but are not physically adjacent - for example
two separate loops that share a trailhead - cannot be detected here. That
limitation is real and is stated in the model report rather than papered over.
Exact duplicate geometry is additionally checked in the leakage audit, and the
retained dataset contains none.
"""

from __future__ import annotations

import re

import pandas as pd


# Coordinates are rounded to this many decimals when comparing endpoints.
# Roughly 0.1 m at the equator: tight enough that only genuinely shared
# vertices match, loose enough to survive floating point noise between
# providers.
ENDPOINT_DECIMALS = 6

_NUMBER_PATTERN = re.compile(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?")


def _endpoints(geometry_wkt: str) -> list[tuple[float, float]]:
    """
    Endpoint coordinates of a line geometry, in either WKT form.

    Whitespace around the parentheses is normalised first, so a MultiLineString
    written with spaces between its parts parses the same as one written
    without. Returns nothing for an empty or unparseable value, so one bad row
    cannot silently merge unrelated groups.
    """
    if not isinstance(geometry_wkt, str) or "(" not in geometry_wkt:
        return []
    # Whitespace around the structural characters is removed so a
    # MultiLineString written with spaces between its parts parses the same as
    # one written without. Whitespace BETWEEN the two ordinates is kept,
    # because "1.0 2.0" and "1.02.0" are different things.
    body = re.sub(
        r"\s*([(),])\s*",
        r"\1",
        geometry_wkt[
            geometry_wkt.index("(") + 1 : geometry_wkt.rindex(")")
        ],
    )
    found: list[tuple[float, float]] = []
    for part in body.split("),("):
        points = part.strip("()").split(",")
        if len(points) < 2:
            continue
        for text in (points[0], points[-1]):
            numbers = _NUMBER_PATTERN.findall(text)
            if len(numbers) < 2:
                continue
            try:
                longitude = round(float(numbers[0]), ENDPOINT_DECIMALS)
                latitude = round(float(numbers[1]), ENDPOINT_DECIMALS)
            except ValueError:
                continue
            found.append((longitude, latitude))
    return found


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, index: int) -> int:
        while self.parent[index] != index:
            self.parent[index] = self.parent[self.parent[index]]
            index = self.parent[index]
        return index

    def union(self, first: int, second: int) -> None:
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root == second_root:
            return
        # Always attach the larger index to the smaller one, so the resulting
        # structure does not depend on the order rows were visited in.
        low, high = sorted((first_root, second_root))
        self.parent[high] = low


def build_split_groups(
    ways: pd.DataFrame,
    geometry_column: str = "geometry_wkt",
) -> pd.Series:
    """
    Return the split group for every row, as a stable string label.

    The label is the lexicographically smallest cell in the connected
    component, so re-running this on the same data always produces the same
    labels regardless of row order.
    """
    cells = [
        (int(row.grid_x), int(row.grid_y))
        for row in ways[["grid_x", "grid_y"]].itertuples(
            index=False
        )
    ]
    unique_cells = sorted(set(cells))
    cell_index = {cell: index for index, cell in enumerate(unique_cells)}
    union = _UnionFind(len(unique_cells))

    owners: dict[tuple[float, float], int] = {}
    for position, raw in enumerate(
        ways[geometry_column].tolist()
    ):
        index = cell_index[cells[position]]
        for endpoint in _endpoints(raw):
            previous = owners.get(endpoint)
            if previous is None:
                owners[endpoint] = index
                continue
            if previous != index:
                union.union(previous, index)

    # Smallest cell label per merged component.
    component_label: dict[int, str] = {}
    for cell, index in cell_index.items():
        root = union.find(index)
        label = f"{cell[0]}:{cell[1]}"
        existing = component_label.get(root)
        if existing is None or label < existing:
            component_label[root] = label

    return pd.Series(
        [
            component_label[union.find(cell_index[cell])]
            for cell in cells
        ],
        index=ways.index,
        dtype=object,
    )



