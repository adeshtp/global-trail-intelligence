"use client";

import { useState } from "react";

import type {
  ElevationMetrics,
  ElevationProfilePoint,
} from "@/app/explore/types";
import { groupProfileComponents } from "@/app/explore/helpers";
import {
  nearestDisplayPointIndex,
  orientDisplayProfile,
  tooltipSide,
} from "@/components/elevationDisplay";

/**
 * Trail elevation profile.
 *
 * Horizontal axis is distance along the selected route, vertical axis is
 * elevation in metres, with real axis ticks. Elevation figures deliberately
 * do NOT appear on the distance axis, which previously made the chart read
 * as unrelated bars.
 *
 * Each disconnected component of the route is drawn as its own polyline. A
 * route whose mapped geometry is several separate pieces would otherwise be
 * drawn as one continuous line bridging the gaps, which asserts a climb and a
 * distance that do not exist on the ground. The horizontal axis is the real
 * cumulative distance, so the pieces sit in the right order with the true gap
 * between them, and nothing is joined across it.
 */
export default function ElevationProfile({
  profile,
  metrics,
  orientation,
}: {
  profile: ElevationProfilePoint[];
  metrics: ElevationMetrics;
  orientation?: {
    profile_orientation?: string;
    reversal_needed?: boolean;
  } | null;
}) {
  const width = 640;
  const height = 190;
  const padLeft = 46;
  const padRight = 10;
  const padTop = 10;
  const padBottom = 22;

  /*
   * Hover readout state: an index into the displayed (possibly
   * low-to-high oriented) points, or null when the pointer is off the
   * chart. Declared before any early return so hook order is stable.
   */
  const [hoverIndex, setHover] = useState<number | null>(null);

  const points = profile
    .filter(
      (point): point is ElevationProfilePoint & {
        elevation_m: number;
      } =>
        typeof point.elevation_m === "number" &&
        point.elevation_m !== null
    )
    .slice(0, 600);

  const components = groupProfileComponents(points);

  if (points.length < 2 || components.length === 0) {
    return (
      <p className="mt-5 text-[11px] text-white/30">
        Not enough elevation samples to draw a profile.
      </p>
    );
  }

  /*
   * Presentation-only orientation. The points above stay in mapped order;
   * when the backend reports strong low-to-high endpoint evidence for a
   * single-piece route, display copies run from the lower end instead.
   * The route geometry and the stored profile are never reordered.
   */
  const oriented = orientDisplayProfile(
    points,
    points[points.length - 1].distance_km || 1,
    metrics.elevation_gain_m,
    metrics.elevation_loss_m,
    orientation ?? null,
    components.length === 1
  );
  const displayPoints = oriented.displayPoints;
  const drawComponents = groupProfileComponents(displayPoints);

  const maxDistance =
    displayPoints[displayPoints.length - 1].distance_km || 1;
  const elevations = displayPoints.map((point) => point.elevation_m);
  let minElevation = Math.min(...elevations);
  let maxElevation = Math.max(...elevations);
  if (maxElevation - minElevation < 1) {
    maxElevation = minElevation + 1;
  }
  const padElevation = (maxElevation - minElevation) * 0.12;
  minElevation -= padElevation;
  maxElevation += padElevation;

  const innerWidth = width - padLeft - padRight;
  const innerHeight = height - padTop - padBottom;

  const x = (distance: number) =>
    padLeft + (distance / (maxDistance || 1)) * innerWidth;
  const y = (elevation: number) =>
    padTop +
    innerHeight -
    ((elevation - minElevation) /
      (maxElevation - minElevation || 1)) *
      innerHeight;

  const baselineY = padTop + innerHeight;

  // One path per component. Nothing is drawn between the last point of one
  // component and the first point of the next.
  const componentPaths = drawComponents.map((component) => {
    const line = component
      .map(
        (point, index) =>
          `${index === 0 ? "M" : "L"}${x(
            point.distance_km
          ).toFixed(1)},${y(point.elevation_m).toFixed(1)}`
      )
      .join(" ");
    const area = `${line} L${x(
      component[component.length - 1].distance_km
    ).toFixed(1)},${baselineY.toFixed(1)} L${x(
      component[0].distance_km
    ).toFixed(1)},${baselineY.toFixed(1)} Z`;
    return { line, area };
  });

  /*
   * Axis ticks share the data scale: fraction 0 is the minimum elevation
   * at the bottom of the plot, fraction 1 the maximum at the top.
   */
  const elevationTicks = [0, 0.5, 1].map((fraction) => ({
    value:
      minElevation +
      (maxElevation - minElevation) * fraction,
    y: padTop + innerHeight * (1 - fraction),
  }));
  const distanceTicks = [0, 0.25, 0.5, 0.75, 1].map(
    (fraction, tickIndex, allTicks) => ({
      value: maxDistance * fraction,
      x: padLeft + innerWidth * fraction,
      anchor:
        tickIndex === 0
          ? "start"
          : tickIndex === allTicks.length - 1
            ? "end"
            : "middle",
    }),
  );

  const gain = oriented.displayGain;
  const loss = oriented.displayLoss;

  /*
   * Hover readout. The tooltip always snaps to a real sampled point, so
   * both values describe one actual sample: its cumulative distance and
   * its elevation, plus which disconnected piece it belongs to. Nothing
   * is interpolated, and nothing bridges a gap between pieces.
   */
  const hoverPoint =
    hoverIndex !== null
      ? (displayPoints[hoverIndex] ?? null)
      : null;
  const hoverX =
    hoverPoint !== null ? x(hoverPoint.distance_km) : 0;
  const hoverY =
    hoverPoint !== null ? y(hoverPoint.elevation_m) : 0;
  const hoverPieceIndex =
    hoverPoint !== null && drawComponents.length > 1
      ? drawComponents.findIndex(
          (component) =>
            component.length > 0 &&
            component[0].component_index ===
              hoverPoint.component_index
        )
      : -1;
  const hoverPiece =
    hoverPieceIndex >= 0 ? hoverPieceIndex + 1 : null;
  const hoverLines =
    hoverPoint !== null
      ? [
          `${hoverPoint.distance_km.toFixed(2)} km`,
          `${Math.round(hoverPoint.elevation_m).toLocaleString("en-US")} m`,
          ...(hoverPiece !== null
            ? [`Piece ${hoverPiece} of ${drawComponents.length}`]
            : []),
        ]
      : [];
  const tooltipBoxWidth = 132;
  const tooltipBoxHeight = 14 + hoverLines.length * 15;
  const tooltipBoxX =
    hoverPoint !== null
      ? Math.min(
          Math.max(
            tooltipSide(hoverX, width - padRight, tooltipBoxWidth) ===
              "right"
              ? hoverX + 10
              : hoverX - 10 - tooltipBoxWidth,
            padLeft
          ),
          width - padRight - tooltipBoxWidth
        )
      : 0;
  const tooltipBoxY =
    hoverPoint !== null
      ? Math.min(
          Math.max(hoverY - tooltipBoxHeight - 10, padTop),
          baselineY - tooltipBoxHeight
        )
      : 0;

  return (
    <div className="mt-5">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label="Elevation profile: horizontal axis is distance along the route, vertical axis is elevation in metres"
      >
        <defs>
          <linearGradient
            id="terrainFill"
            x1="0"
            y1="0"
            x2="0"
            y2="1"
          >
            <stop
              offset="0%"
              stopColor="#34d399"
              stopOpacity="0.45"
            />
            <stop
              offset="100%"
              stopColor="#34d399"
              stopOpacity="0.05"
            />
          </linearGradient>
        </defs>

        {elevationTicks.map((tick) => (
          <g key={`e-${tick.value.toFixed(0)}`}>
            <line
              x1={padLeft}
              x2={width - padRight}
              y1={tick.y}
              y2={tick.y}
              stroke="rgba(255,255,255,0.07)"
              strokeWidth={1}
            />
            <text
              x={padLeft - 6}
              y={tick.y + 3}
              textAnchor="end"
              className="fill-white/35"
              style={{ fontSize: 9 }}
            >
              {Math.round(tick.value)} m
            </text>
          </g>
        ))}

        {componentPaths.map((path, index) => (
          <g key={`c-${index}`}>
            <path d={path.area} fill="url(#terrainFill)" />
            <path
              d={path.line}
              fill="none"
              stroke="#6ee7b7"
              strokeWidth={1.8}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </g>
        ))}

        {distanceTicks.map((tick) => (
          <text
            key={`d-${tick.value.toFixed(2)}`}
            x={tick.x}
            y={height - 6}
            textAnchor={tick.anchor as "start" | "middle" | "end"}
            className="fill-white/35"
            style={{ fontSize: 9 }}
          >
            {tick.value.toFixed(1)} km
          </text>
        ))}

        {hoverPoint !== null ? (
          <g>
            <line
              x1={hoverX}
              x2={hoverX}
              y1={padTop}
              y2={baselineY}
              stroke="rgba(255,255,255,0.35)"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <circle
              cx={hoverX}
              cy={hoverY}
              r={4}
              fill="#0b1724"
              stroke="#6ee7b7"
              strokeWidth={2}
            />
            <rect
              x={tooltipBoxX}
              y={tooltipBoxY}
              width={tooltipBoxWidth}
              height={tooltipBoxHeight}
              rx={8}
              fill="#0b1724"
              fillOpacity={0.94}
              stroke="rgba(255,255,255,0.18)"
              strokeWidth={1}
            />
            {hoverLines.map((line, lineIndex) => (
              <text
                key={`h-${lineIndex}`}
                x={tooltipBoxX + 10}
                y={tooltipBoxY + 17 + lineIndex * 15}
                className="fill-white/90"
                style={{ fontSize: 11, fontWeight: 600 }}
              >
                {line}
              </text>
            ))}
          </g>
        ) : null}

        <rect
          x={padLeft}
          y={padTop}
          width={innerWidth}
          height={innerHeight}
          fill="transparent"
          style={{ cursor: "crosshair" }}
          onPointerMove={(event) => {
            const svg = event.currentTarget.ownerSVGElement;
            if (!svg) {
              return;
            }
            const rect = svg.getBoundingClientRect();
            if (rect.width <= 0) {
              return;
            }
            const svgX =
              (event.clientX - rect.left) * (width / rect.width);
            const target =
              ((svgX - padLeft) / innerWidth) * maxDistance;
            setHover(nearestDisplayPointIndex(displayPoints, target));
          }}
          onPointerLeave={() => setHover(null)}
        />
      </svg>

      {drawComponents.length > 1 ? (
        <p className="mt-2 text-[10px] leading-4 text-white/30">
          {drawComponents.length} disconnected pieces are drawn
          separately. The gaps between them are not distance, and
          no climb is counted across them.
        </p>
      ) : null}

      <p className="mt-2 text-[10px] leading-4 text-white/30">
        {oriented.directionNote}
      </p>

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {(
          [
            [
              "Distance",
              `${maxDistance.toFixed(2)} km`,
            ],
            [
              "Ascent",
              gain !== null && gain !== undefined
                ? `${Math.round(gain)} m`
                : "Not available",
            ],
            [
              "Descent",
              loss !== null && loss !== undefined
                ? `${Math.round(loss)} m`
                : "Not available",
            ],
            [
              "Elevation range",
              metrics.elevation_range_m !== null &&
              metrics.elevation_range_m !== undefined
                ? `${Math.round(metrics.elevation_range_m)} m`
                : "Not available",
            ],
          ] as const
        ).map(
          ([label, value]) => (
            <div
              key={label}
              className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2"
            >
              <p className="text-[9px] uppercase tracking-[0.12em] text-white/35">
                {label}
              </p>
              <p className="mt-0.5 text-[13px] font-semibold text-white/80">
                {value}
              </p>
            </div>
          )
        )}
      </div>
    </div>
  );
}
