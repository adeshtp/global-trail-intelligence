"use client";

import {
  useEffect,
  useRef,
  useState,
} from "react";

import "cesium/Build/Cesium/Widgets/widgets.css";

type Location = {
  latitude: number;
  longitude: number;
};

type MapTrail = {
  osm_id: number;

  osm_type:
    | "way"
    | "relation"
    | "component";

  geometry: {
    type:
      | "LineString"
      | "MultiLineString";

    coordinates:
      | number[][]
      | number[][][];
  };
};

type TrailGeometry = {
  osm_id: number;

  osm_type:
    | "way"
    | "relation"
    | "component"
    | string;

  name: string | null;

  route_type: string | null;
  highway_type: string | null;

  description: string | null;

  difficulty: string | null;
  surface: string | null;
  trail_visibility: string | null;

  distance_km: number;

  geometry: {
    type:
      | "LineString"
      | "MultiLineString"
      | string;

    coordinates:
      | number[][]
      | number[][][];
  };
};

type CesiumMapProps = {
  location: Location | null;

  mapTrails: MapTrail[];

  selectedTrail:
    | TrailGeometry
    | null;

  expanded?: boolean;

  onExpand?: () => void;

  onCollapse?: () => void;
};

type ViewState =
  | "map"
  | "terrain";

export default function CesiumMap({
  location,
  mapTrails,
  selectedTrail,
  expanded = false,
  onExpand,
  onCollapse,
}: CesiumMapProps) {
  const containerRef =
    useRef<HTMLDivElement | null>(
      null
    );

  const viewerRef =
    useRef<any>(null);

  const [viewerReady, setViewerReady] =
    useState(false);

  const [layersOpen, setLayersOpen] =
    useState(false);

  const [viewState, setViewState] =
    useState<ViewState>(
      "map"
    );

  const locationRequestRef =
    useRef(0);

  const trailRequestRef =
    useRef(0);

  /* ==========================================================
     INITIALIZE CESIUM
  ========================================================== */

  useEffect(() => {
    let cancelled = false;

    async function initialize() {
      if (
        !containerRef.current ||
        viewerRef.current
      ) {
        return;
      }

      (
        window as typeof window & {
          CESIUM_BASE_URL: string;
        }
      ).CESIUM_BASE_URL =
        "/cesium/";

      const Cesium =
        await import("cesium");

      if (
        cancelled ||
        !containerRef.current
      ) {
        return;
      }

      const token =
        process.env
          .NEXT_PUBLIC_CESIUM_ION_TOKEN;

      if (token) {
        Cesium.Ion.defaultAccessToken =
          token;
      }

      const viewer =
        new Cesium.Viewer(
          containerRef.current,
          {
            terrain: token
              ? Cesium.Terrain.fromWorldTerrain()
              : undefined,

            animation:
              false,

            timeline:
              false,

            baseLayerPicker:
              false,

            geocoder:
              false,

            homeButton:
              false,

            sceneModePicker:
              false,

            navigationHelpButton:
              false,

            fullscreenButton:
              false,

            infoBox:
              false,

            selectionIndicator:
              false,

            shadows:
              false,

            requestRenderMode:
              true,

            maximumRenderTimeChange:
              Infinity,
          }
        );

      viewerRef.current =
        viewer;

      const bottomContainer =
        viewer.bottomContainer as
          | HTMLElement
          | undefined;

      if (
        bottomContainer
      ) {
        bottomContainer.style.display =
          "none";
      }

      /* ------------------------------------------------------
         OSM base layer
      ------------------------------------------------------ */

      const osmProvider =
        new Cesium.UrlTemplateImageryProvider(
          {
            url:
              "https://tile.openstreetmap.org/{z}/{x}/{y}.png",

            credit:
              "© OpenStreetMap contributors",

            maximumLevel:
              19,
          }
        );

      viewer.imageryLayers.removeAll();

      viewer.imageryLayers.addImageryProvider(
        osmProvider
      );

      /* ------------------------------------------------------
         Camera controller
      ------------------------------------------------------ */

      const controller =
        viewer.scene
          .screenSpaceCameraController;

      controller.inertiaSpin =
        0;

      controller.inertiaTranslate =
        0.01;

      controller.inertiaZoom =
        0.02;

      controller.minimumZoomDistance =
        40;

      controller.maximumZoomDistance =
        50000000;

      controller.enableCollisionDetection =
        true;

      controller.enableInputs =
        true;

      /* ------------------------------------------------------
         Globe
      ------------------------------------------------------ */

      viewer.scene.globe.show =
        true;

      viewer.scene.globe.enableLighting =
        false;

      viewer.scene.globe.depthTestAgainstTerrain =
        true;

      /*
       * NORMAL STATE:
       * top-down world map.
       *
       * It should feel like a standard map,
       * not like an uncontrolled 3D globe.
       */
      viewer.camera.setView({
        destination:
          Cesium.Cartesian3.fromDegrees(
            0,
            20,
            18000000
          ),

        orientation: {
          heading:
            0,

          pitch:
            Cesium.Math.toRadians(
              -90
            ),

          roll:
            0,
        },
      });

      if (
        !viewer.isDestroyed()
      ) {
        viewer.resize();
        viewer.scene.requestRender();
      }

      if (!cancelled) {
        setViewerReady(true);
      }
    }

    initialize().catch(
      (error) => {
        console.error(
          "Cesium initialization failed:",
          error
        );
      }
    );

    return () => {
      cancelled =
        true;

      const viewer =
        viewerRef.current;

      if (
        viewer &&
        !viewer.isDestroyed()
      ) {
        viewer.destroy();
      }

      viewerRef.current =
        null;

      setViewerReady(
        false
      );
    };
  }, []);

  /* ==========================================================
     RESIZE
  ========================================================== */

  useEffect(() => {
    const element =
      containerRef.current;

    if (!element) {
      return;
    }

    const observer =
      new ResizeObserver(
        () => {
          const viewer =
            viewerRef.current;

          if (
            viewer &&
            !viewer.isDestroyed()
          ) {
            viewer.resize();

            viewer.scene.requestRender();
          }
        }
      );

    observer.observe(
      element
    );

    return () => {
      observer.disconnect();
    };
  }, []);

  /* ==========================================================
     SEARCH LOCATION
  ========================================================== */

  useEffect(() => {
    if (
      !viewerReady ||
      !location
    ) {
      return;
    }

    const requestId =
      ++locationRequestRef.current;

    const currentLocation =
      location;

    async function moveToLocation() {
      const viewer =
        viewerRef.current;

      if (
        !viewer ||
        viewer.isDestroyed()
      ) {
        return;
      }

      const Cesium =
        await import("cesium");

      if (
        requestId !==
        locationRequestRef.current
      ) {
        return;
      }

      const latitude =
        Number(
          currentLocation.latitude
        );

      const longitude =
        Number(
          currentLocation.longitude
        );

      if (
        !Number.isFinite(
          latitude
        ) ||
        !Number.isFinite(
          longitude
        )
      ) {
        return;
      }

      /*
       * Remove previous marker.
       */
      const oldMarker =
        viewer.entities.getById(
          "search-location"
        );

      if (oldMarker) {
        viewer.entities.remove(
          oldMarker
        );
      }

      /*
       * EXACT search point.
       */
      viewer.entities.add({
        id:
          "search-location",

        name:
          "Searched location",

        position:
          Cesium.Cartesian3.fromDegrees(
            longitude,
            latitude,
            0
          ),

        point: {
          pixelSize:
            10,

          color:
            Cesium.Color.WHITE,

          outlineColor:
            Cesium.Color.fromCssColorString(
              "#17283a"
            ),

          outlineWidth:
            3,

          heightReference:
            Cesium.HeightReference.CLAMP_TO_GROUND,

          disableDepthTestDistance:
            Number.POSITIVE_INFINITY,
        },

        label: {
          text:
            "Searched location",

          font:
            "12px sans-serif",

          fillColor:
            Cesium.Color.WHITE,

          showBackground:
            true,

          backgroundColor:
            Cesium.Color.fromCssColorString(
              "#07111f"
            ).withAlpha(
              0.88
            ),

          backgroundPadding:
            new Cesium.Cartesian2(
              8,
              5
            ),

          pixelOffset:
            new Cesium.Cartesian2(
              0,
              -24
            ),

          disableDepthTestDistance:
            Number.POSITIVE_INFINITY,
        },
      });

      /*
       * EXACT center.
       *
       * Top-down camera removes the visual offset that
       * occurred with the previous oblique pitch.
       */
      viewer.camera.flyTo({
        destination:
          Cesium.Cartesian3.fromDegrees(
            longitude,
            latitude,
            9000
          ),

        orientation: {
          heading:
            0,

          pitch:
            Cesium.Math.toRadians(
              -90
            ),

          roll:
            0,
        },

        duration:
          1.1,

        complete:
          () => {
            if (
              !viewer.isDestroyed()
            ) {
              viewer.scene.requestRender();
            }
          },
      });

      setViewState(
        "map"
      );
    }

    moveToLocation().catch(
      (error) => {
        console.error(
          "Location camera movement failed:",
          error
        );
      }
    );
  }, [
    location,
    viewerReady,
  ]);

  /* ==========================================================
     DRAW ALL AVAILABLE TRAILS
     
     These are the trails around the searched location,
     not fake routes and not selected-trail replacements.
  ========================================================== */

  useEffect(() => {
    if (!viewerReady) {
      return;
    }

    const requestId =
      ++trailRequestRef.current;

    async function drawMapTrails() {
      const viewer =
        viewerRef.current;

      if (
        !viewer ||
        viewer.isDestroyed()
      ) {
        return;
      }

      const Cesium =
        await import("cesium");

      /*
       * Remove previous discovery geometry.
       */
      viewer.entities.values
        .slice()
        .forEach(
          (entity: any) => {
            if (
              typeof entity.id ===
                "string" &&
              entity.id.startsWith(
                "discovered-trail-"
              )
            ) {
              viewer.entities.remove(
                entity
              );
            }
          }
        );

      if (
        mapTrails.length ===
        0
      ) {
        viewer.scene.requestRender();
        return;
      }

      /*
       * Draw lightweight,
       * non-selected trail lines.
       */
      mapTrails.forEach(
        (
          trail,
          trailIndex
        ) => {
          if (
            !trail.geometry
          ) {
            return;
          }

          let segments:
            number[][][] = [];

          if (
            trail.geometry.type ===
            "LineString"
          ) {
            segments = [
              trail.geometry.coordinates as number[][],
            ];
          } else if (
            trail.geometry.type ===
            "MultiLineString"
          ) {
            segments =
              trail.geometry.coordinates as number[][][];
          }

          segments.forEach(
            (
              segment,
              segmentIndex
            ) => {
              const positions =
                segment
                  .filter(
                    (
                      coordinate
                    ) =>
                      coordinate &&
                      coordinate.length >=
                        2 &&
                      Number.isFinite(
                        Number(
                          coordinate[0]
                        )
                      ) &&
                      Number.isFinite(
                        Number(
                          coordinate[1]
                        )
                      )
                  )
                  .map(
                    (
                      coordinate
                    ) =>
                      Cesium.Cartesian3.fromDegrees(
                        Number(
                          coordinate[0]
                        ),
                        Number(
                          coordinate[1]
                        ),
                        0
                      )
                  );

              if (
                positions.length <
                2
              ) {
                return;
              }

              viewer.entities.add({
                id:
                  `discovered-trail-${trail.osm_type}-${trail.osm_id}-${trailIndex}-${segmentIndex}`,

                name:
                  "Available trail",

                polyline: {
                  positions,

                  width:
                    3,

                  clampToGround:
                    true,

                  material:
                    Cesium.Color.fromCssColorString(
                      "#e8eef3"
                    ).withAlpha(
                      0.82
                    ),
                },
              });
            }
          );
        }
      );

      viewer.scene.requestRender();

      if (
        requestId !==
        trailRequestRef.current
      ) {
        return;
      }
    }

    drawMapTrails().catch(
      (error) => {
        console.error(
          "Trail map rendering failed:",
          error
        );
      }
    );
  }, [
    mapTrails,
    viewerReady,
  ]);

  /* ==========================================================
     SELECTED TRAIL
  ========================================================== */

  useEffect(() => {
    if (!viewerReady) {
      return;
    }

    const requestId =
      ++trailRequestRef.current;

    async function drawSelectedTrail() {
      const viewer =
        viewerRef.current;

      if (
        !viewer ||
        viewer.isDestroyed()
      ) {
        return;
      }

      const Cesium =
        await import("cesium");

      /*
       * Remove old selected geometry.
       */
      viewer.entities.values
        .slice()
        .forEach(
          (entity: any) => {
            if (
              typeof entity.id ===
                "string" &&
              entity.id.startsWith(
                "selected-trail-"
              )
            ) {
              viewer.entities.remove(
                entity
              );
            }
          }
        );

      if (
        !selectedTrail
      ) {
        viewer.scene.requestRender();
        return;
      }

      if (
        requestId !==
        trailRequestRef.current
      ) {
        return;
      }

      let segments:
        number[][][] = [];

      if (
        selectedTrail.geometry.type ===
        "LineString"
      ) {
        segments = [
          selectedTrail.geometry.coordinates as number[][],
        ];
      } else if (
        selectedTrail.geometry.type ===
        "MultiLineString"
      ) {
        segments =
          selectedTrail.geometry.coordinates as number[][][];
      }

      const allPositions:
        any[] = [];

      segments.forEach(
        (
          segment,
          segmentIndex
        ) => {
          const positions =
            segment
              .filter(
                (
                  coordinate
                ) =>
                  coordinate &&
                  coordinate.length >=
                    2 &&
                  Number.isFinite(
                    Number(
                      coordinate[0]
                    )
                  ) &&
                  Number.isFinite(
                    Number(
                      coordinate[1]
                    )
                  )
              )
              .map(
                (
                  coordinate
                ) =>
                  Cesium.Cartesian3.fromDegrees(
                    Number(
                      coordinate[0]
                    ),
                    Number(
                      coordinate[1]
                    ),
                    0
                  )
              );

          if (
            positions.length <
            2
          ) {
            return;
          }

          allPositions.push(
            ...positions
          );

          viewer.entities.add({
            id:
              `selected-trail-${segmentIndex}`,

            name:
              selectedTrail.name ??
              "Selected trail",

            polyline: {
              positions,

              width:
                expanded
                  ? 8
                  : 7,

              clampToGround:
                true,

              material:
                new Cesium.PolylineGlowMaterialProperty(
                  {
                    glowPower:
                      0.16,

                    color:
                      Cesium.Color.fromCssColorString(
                        "#ff9f43"
                      ),
                  }
                ),
            },
          });
        }
      );

      if (
        allPositions.length <
        2
      ) {
        return;
      }

      /*
       * Camera now follows the ACTUAL selected trail.
       */
      const boundingSphere =
        Cesium.BoundingSphere.fromPoints(
          allPositions
        );

      const range =
        Math.max(
          boundingSphere.radius *
            2.4,
          1000
        );

      viewer.camera.flyToBoundingSphere(
        boundingSphere,
        {
          duration:
            1.4,

          offset:
            new Cesium.HeadingPitchRange(
              0,

              Cesium.Math.toRadians(
                -48
              ),

              range
            ),
        }
      );

      viewer.scene.requestRender();
    }

    drawSelectedTrail().catch(
      (error) => {
        console.error(
          "Selected trail rendering failed:",
          error
        );
      }
    );
  }, [
    selectedTrail,
    viewerReady,
    expanded,
  ]);

  /* ==========================================================
     NORMAL MAP CONTROLS
  ========================================================== */

  async function zoomIn() {
    const viewer =
      viewerRef.current;

    if (!viewer) {
      return;
    }

    const Cesium =
      await import("cesium");

    viewer.camera.zoomIn(
      Math.max(
        viewer.camera.positionCartographic.height *
          0.35,
        100
      )
    );

    viewer.scene.requestRender();
  }

  async function zoomOut() {
    const viewer =
      viewerRef.current;

    if (!viewer) {
      return;
    }

    const Cesium =
      await import("cesium");

    viewer.camera.zoomOut(
      Math.max(
        viewer.camera.positionCartographic.height *
          0.35,
        100
      )
    );

    viewer.scene.requestRender();
  }

  async function resetNorth() {
    const viewer =
      viewerRef.current;

    if (!viewer) {
      return;
    }

    const Cesium =
      await import("cesium");

    viewer.camera.setView({
      orientation: {
        heading:
          0,

        pitch:
          viewer.camera.pitch,

        roll:
          0,
      },
    });

    viewer.scene.requestRender();
  }

  async function centerLocation() {
    const currentLocation =
      location;

    if (!currentLocation) {
      return;
    }

    const viewer =
      viewerRef.current;

    if (!viewer) {
      return;
    }

    const Cesium =
      await import("cesium");

    viewer.camera.flyTo({
      destination:
        Cesium.Cartesian3.fromDegrees(
          currentLocation.longitude,
          currentLocation.latitude,
          viewState ===
          "terrain"
            ? 11000
            : 9000
        ),

      orientation: {
        heading:
          0,

        pitch:
          viewState ===
          "terrain"
            ? Cesium.Math.toRadians(
                -58
              )
            : Cesium.Math.toRadians(
                -90
              ),

        roll:
          0,
      },

      duration:
        0.8,
    });
  }

  /* ==========================================================
     TRUE 3D TERRAIN
  ========================================================== */

  async function toggle3DTerrain() {
    const viewer =
      viewerRef.current;

    if (!viewer) {
      return;
    }

    const currentLocation =
      location;

    const token =
      process.env
        .NEXT_PUBLIC_CESIUM_ION_TOKEN;

    if (!token) {
      console.error(
        "NEXT_PUBLIC_CESIUM_ION_TOKEN is missing."
      );

      return;
    }

    const Cesium =
      await import("cesium");

    if (
      viewState ===
      "map"
    ) {
      try {
        if (
          viewer.scene.mode !==
          Cesium.SceneMode.SCENE3D
        ) {
          viewer.scene.morphTo3D(
            0.7
          );
        }

        viewer.terrainProvider =
          await Cesium.createWorldTerrainAsync();

        setViewState(
          "terrain"
        );

        if (currentLocation) {
          viewer.camera.flyTo({
            destination:
              Cesium.Cartesian3.fromDegrees(
                currentLocation.longitude,
                currentLocation.latitude,
                11000
              ),

            orientation: {
              heading:
                0,

              pitch:
                Cesium.Math.toRadians(
                  -58
                ),

              roll:
                0,
            },

            duration:
              0.9,
          });
        }
      } catch (error) {
        console.error(
          "3D terrain activation failed:",
          error
        );
      }
    } else {
      /*
       * Return to the map-like top-down view.
       */
      viewer.camera.flyTo({
        destination:
          currentLocation
            ? Cesium.Cartesian3.fromDegrees(
                currentLocation.longitude,
                currentLocation.latitude,
                9000
              )
            : Cesium.Cartesian3.fromDegrees(
                0,
                20,
                18000000
              ),

        orientation: {
          heading:
            0,

          pitch:
            Cesium.Math.toRadians(
              -90
            ),

          roll:
            0,
        },

        duration:
          0.8,
      });

      setViewState(
        "map"
      );
    }

    viewer.scene.requestRender();
  }

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 h-full w-full overflow-hidden bg-[#d8dde2]"
    >
      {/* ======================================================
          MAP BUTTONS
      ====================================================== */}

      <div className="absolute right-4 top-4 z-50 flex gap-2">
        <button
          type="button"
          onClick={() =>
            setLayersOpen(
              (open) => !open
            )
          }
          className="rounded-xl border border-white/20 bg-[#07111f]/90 px-4 py-2.5 text-xs font-medium text-white shadow-xl backdrop-blur-xl transition hover:bg-[#0b1929]"
        >
          Layers
        </button>

        <button
          type="button"
          onClick={
            toggle3DTerrain
          }
          className={[
            "rounded-xl border px-4 py-2.5 text-xs font-semibold shadow-xl backdrop-blur-xl transition",
            viewState ===
            "terrain"
              ? "border-[#ff9f43]/50 bg-[#ff9f43]/15 text-[#ffd3a8]"
              : "border-white/20 bg-[#07111f]/90 text-white hover:bg-[#0b1929]",
          ].join(" ")}
        >
          {viewState ===
          "terrain"
            ? "Map view"
            : "3D terrain"}
        </button>
      </div>

      {/* ======================================================
          LAYERS
      ====================================================== */}

      {layersOpen && (
        <div className="absolute right-4 top-[64px] z-50 w-[280px] rounded-2xl border border-white/10 bg-[#07111f]/95 p-4 shadow-2xl backdrop-blur-xl">
          <p className="text-[10px] font-medium uppercase tracking-[0.2em] text-white/40">
            Map layers
          </p>

          <div className="mt-4 space-y-2">
            <div className="flex items-center justify-between rounded-xl bg-white/[0.05] px-3 py-3">
              <span className="text-xs text-white/80">
                OpenStreetMap
              </span>

              <span className="text-[9px] uppercase tracking-[0.12em] text-white/30">
                Base
              </span>
            </div>

            <div className="flex items-center justify-between rounded-xl bg-white/[0.05] px-3 py-3">
              <span className="text-xs text-white/80">
                Searched location
              </span>

              <span className="text-[9px] uppercase tracking-[0.12em] text-white/30">
                {location
                  ? "Active"
                  : "None"}
              </span>
            </div>

            <div className="flex items-center justify-between rounded-xl bg-white/[0.05] px-3 py-3">
              <span className="text-xs text-white/80">
                Available trails
              </span>

              <span className="text-[9px] uppercase tracking-[0.12em] text-white/30">
                {mapTrails.length}
              </span>
            </div>

            <div className="flex items-center justify-between rounded-xl bg-white/[0.05] px-3 py-3">
              <span className="text-xs text-white/80">
                Selected trail
              </span>

              <span className="text-[9px] uppercase tracking-[0.12em] text-white/30">
                {selectedTrail
                  ? "Active"
                  : "None"}
              </span>
            </div>

            <div className="rounded-xl bg-white/[0.035] px-3 py-3">
              <p className="text-[10px] leading-4 text-white/35">
                Terrain and environmental layers will be
                added here as the intelligence pipeline is
                integrated.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ======================================================
          MAP NAVIGATION
      ====================================================== */}

      <div className="absolute bottom-4 left-4 z-50 flex flex-col overflow-hidden rounded-2xl border border-white/15 bg-[#07111f]/90 shadow-2xl backdrop-blur-xl">
        <button
          type="button"
          onClick={
            zoomIn
          }
          aria-label="Zoom in"
          className="flex h-10 w-10 items-center justify-center border-b border-white/10 text-lg text-white/80 transition hover:bg-white/10"
        >
          +
        </button>

        <button
          type="button"
          onClick={
            zoomOut
          }
          aria-label="Zoom out"
          className="flex h-10 w-10 items-center justify-center border-b border-white/10 text-lg text-white/80 transition hover:bg-white/10"
        >
          −
        </button>

        <button
          type="button"
          onClick={
            resetNorth
          }
          aria-label="Reset north"
          className="flex h-10 w-10 items-center justify-center border-b border-white/10 text-[11px] font-semibold text-white/80 transition hover:bg-white/10"
        >
          N
        </button>

        <button
          type="button"
          onClick={
            centerLocation
          }
          aria-label="Center searched location"
          className="flex h-10 w-10 items-center justify-center text-lg text-white/70 transition hover:bg-white/10"
        >
          ◎
        </button>
      </div>

      {/* ======================================================
          EXPAND / CLOSE
      ====================================================== */}

      {!expanded &&
        onExpand && (
          <button
            type="button"
            onClick={
              onExpand
            }
            className="absolute bottom-4 right-4 z-50 rounded-xl border border-white/20 bg-[#07111f]/90 px-4 py-2.5 text-xs font-medium text-white shadow-xl backdrop-blur-xl transition hover:bg-[#0b1929]"
          >
            Expand map ↗
          </button>
        )}

      {expanded &&
        onCollapse && (
          <button
            type="button"
            onClick={
              onCollapse
            }
            className="absolute left-4 top-4 z-50 rounded-xl border border-white/20 bg-[#07111f]/90 px-4 py-2.5 text-xs font-medium text-white shadow-xl backdrop-blur-xl transition hover:bg-[#0b1929]"
          >
            Close
          </button>
        )}

      {/* ======================================================
          ATTRIBUTION
      ====================================================== */}

      <div className="absolute bottom-1 right-1 z-40 rounded bg-white/80 px-2 py-1 text-[9px] text-black/60 backdrop-blur-sm">
        © OpenStreetMap contributors
      </div>
    </div>
  );
}