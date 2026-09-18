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

  start_coordinate?: [
    number,
    number
  ] | null;

  end_coordinate?: [
    number,
    number
  ] | null;

  endpoint_available?: boolean;

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

type BaseMap =
  | "satellite"
  | "osm";

type Coordinate = [
  number,
  number
];

/*
 * ============================================================
 * FINAL MAP COLORS
 * ============================================================
 *
 * Main route:
 * bright hiking green
 *
 * This is deliberately NOT dark green.
 */

const TRAIL_GREEN =
  "#63E96B";

const TRAIL_CASING =
  "#10231A";

const OTHER_TRAIL =
  "#C8E8CF";

const START_GREEN =
  "#16A34A";

const END_RED =
  "#EF4444";

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

  const osmLayerRef =
    useRef<any>(null);

  const satelliteLayerRef =
    useRef<any>(null);

  const [
    viewerReady,
    setViewerReady,
  ] = useState(false);

  const [
    layersOpen,
    setLayersOpen,
  ] = useState(false);

  const [
    viewState,
    setViewState,
  ] = useState<ViewState>(
    "map"
  );

  const [
    baseMap,
    setBaseMap,
  ] = useState<BaseMap>(
    "osm"
  );

  const [
    satelliteAvailable,
    setSatelliteAvailable,
  ] = useState(false);

  const locationRequestRef =
    useRef(0);

  /*
   * ============================================================
   * GEOMETRY HELPERS
   * ============================================================
   */

  function geometryToSegments(
    geometry:
      | TrailGeometry["geometry"]
      | MapTrail["geometry"]
  ): Coordinate[][] {
    if (!geometry) {
      return [];
    }

    if (
      geometry.type ===
      "LineString"
    ) {
      const segment =
        geometry.coordinates as number[][];

      return [
        segment
          .filter(
            (coordinate) =>
              Array.isArray(
                coordinate
              ) &&
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
            (coordinate) =>
              [
                Number(
                  coordinate[0]
                ),
                Number(
                  coordinate[1]
                ),
              ] as Coordinate
          ),
      ].filter(
        (segment) =>
          segment.length >= 2
      );
    }

    if (
      geometry.type ===
      "MultiLineString"
    ) {
      return (
        geometry.coordinates as number[][][]
      )
        .map(
          (segment) =>
            segment
              .filter(
                (coordinate) =>
                  Array.isArray(
                    coordinate
                  ) &&
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
                (coordinate) =>
                  [
                    Number(
                      coordinate[0]
                    ),
                    Number(
                      coordinate[1]
                    ),
                  ] as Coordinate
              )
        )
        .filter(
          (segment) =>
            segment.length >= 2
        );
    }

    return [];
  }

  /*
   * ============================================================
   * SIMPLE ROUTE BOUNDS
   * ============================================================
   */

  function calculateRouteBounds(
    coordinates: Coordinate[]
  ) {
    if (
      coordinates.length ===
      0
    ) {
      return null;
    }

    let west = Infinity;
    let east = -Infinity;
    let south = Infinity;
    let north = -Infinity;

    coordinates.forEach(
      ([
        longitude,
        latitude,
      ]) => {
        west =
          Math.min(
            west,
            longitude
          );

        east =
          Math.max(
            east,
            longitude
          );

        south =
          Math.min(
            south,
            latitude
          );

        north =
          Math.max(
            north,
            latitude
          );
      }
    );

    return {
      west,
      east,
      south,
      north,

      centerLongitude:
        (
          west +
          east
        ) /
        2,

      centerLatitude:
        (
          south +
          north
        ) /
        2,
    };
  }

  /*
   * ============================================================
   * REMOVE ENTITIES
   * ============================================================
   */

  function removeEntitiesByPrefix(
    prefix: string
  ) {
    const viewer =
      viewerRef.current;

    if (
      !viewer ||
      viewer.isDestroyed()
    ) {
      return;
    }

    viewer.entities.values
      .slice()
      .forEach(
        (entity: any) => {
          if (
            typeof entity.id ===
              "string" &&
            entity.id.startsWith(
              prefix
            )
          ) {
            viewer.entities.remove(
              entity
            );
          }
        }
      );
  }

  /*
   * ============================================================
   * CESIUM INITIALIZATION
   * ============================================================
   */

  useEffect(() => {
    let cancelled =
      false;

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
        await import(
          "cesium"
        );

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
            terrain:
              token
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

      /*
       * OSM basemap.
       */

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

      const osmLayer =
        viewer.imageryLayers.addImageryProvider(
          osmProvider
        );

      osmLayerRef.current =
        osmLayer;

      /*
       * Satellite.
       */

      if (token) {
        try {
          const satelliteProvider =
            await Cesium.createWorldImageryAsync(
              {
                style:
                  Cesium.IonWorldImageryStyle.AERIAL,
              }
            );

          if (
            cancelled ||
            viewer.isDestroyed()
          ) {
            return;
          }

          const satelliteLayer =
            viewer.imageryLayers.addImageryProvider(
              satelliteProvider
            );

          satelliteLayerRef.current =
            satelliteLayer;

          satelliteLayer.show =
            true;

          osmLayer.show =
            false;

          setSatelliteAvailable(
            true
          );

          setBaseMap(
            "satellite"
          );
        } catch (
          error
        ) {
          console.warn(
            "Satellite imagery unavailable.",
            error
          );

          setSatelliteAvailable(
            false
          );

          setBaseMap(
            "osm"
          );
        }
      }

      /*
       * Navigation controls.
       */

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

      viewer.scene.globe.show =
        true;

      viewer.scene.globe.enableLighting =
        false;

      viewer.scene.globe.depthTestAgainstTerrain =
        true;

      /*
       * Initial world camera.
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

      viewer.resize();

      viewer.scene.requestRender();

      if (!cancelled) {
        setViewerReady(
          true
        );
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

      osmLayerRef.current =
        null;

      satelliteLayerRef.current =
        null;
    };
  }, []);

  /*
   * ============================================================
   * RESIZE
   * ============================================================
   */

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

  /*
   * ============================================================
   * BASEMAP SWITCHING
   * ============================================================
   */

  useEffect(() => {
    if (!viewerReady) {
      return;
    }

    const satellite =
      satelliteLayerRef.current;

    const osm =
      osmLayerRef.current;

    if (
      baseMap ===
      "satellite"
    ) {
      if (satellite) {
        satellite.show =
          true;
      }

      if (osm) {
        osm.show =
          false;
      }
    } else {
      if (satellite) {
        satellite.show =
          false;
      }

      if (osm) {
        osm.show =
          true;
      }
    }

    const viewer =
      viewerRef.current;

    if (
      viewer &&
      !viewer.isDestroyed()
    ) {
      viewer.scene.requestRender();
    }
  }, [
    baseMap,
    viewerReady,
  ]);

  /*
   * ============================================================
   * SEARCH LOCATION
   * ============================================================
   */

  useEffect(() => {
    if (
      !viewerReady ||
      !location
    ) {
      return;
    }

    const currentLocation =
      location;

    const requestId =
      ++locationRequestRef.current;

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
        await import(
          "cesium"
        );

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

      const oldMarker =
        viewer.entities.getById(
          "search-location"
        );

      if (oldMarker) {
        viewer.entities.remove(
          oldMarker
        );
      }

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
            Cesium.HeightReference
              .CLAMP_TO_GROUND,

          disableDepthTestDistance:
            Number.POSITIVE_INFINITY,
        },
      });

      viewer.camera.flyTo({
        destination:
          Cesium.Cartesian3.fromDegrees(
            longitude,
            latitude,
            10000
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
          1,
      });

      setViewState(
        "map"
      );
    }

    moveToLocation().catch(
      console.error
    );
  }, [
    location,
    viewerReady,
  ]);

  /*
   * ============================================================
   * OTHER TRAILS
   * ============================================================
   */

  useEffect(() => {
    if (!viewerReady) {
      return;
    }

    async function drawOtherTrails() {
      const viewer =
        viewerRef.current;

      if (
        !viewer ||
        viewer.isDestroyed()
      ) {
        return;
      }

      const Cesium =
        await import(
          "cesium"
        );

      removeEntitiesByPrefix(
        "discovered-trail-"
      );

      mapTrails.forEach(
        (
          trail,
          trailIndex
        ) => {
          const selected =
            selectedTrail !==
              null &&
            selectedTrail.osm_id ===
              trail.osm_id &&
            selectedTrail.osm_type ===
              trail.osm_type;

          if (selected) {
            return;
          }

          const segments =
            geometryToSegments(
              trail.geometry
            );

          segments.forEach(
            (
              segment,
              segmentIndex
            ) => {
              if (
                segment.length <
                2
              ) {
                return;
              }

              const positions =
                segment.map(
                  (
                    coordinate
                  ) =>
                    Cesium.Cartesian3.fromDegrees(
                      coordinate[0],
                      coordinate[1],
                      0
                    )
                );

              viewer.entities.add({
                id:
                  `discovered-trail-${trail.osm_type}-${trail.osm_id}-${trailIndex}-${segmentIndex}`,

                polyline: {
                  positions,

                  width:
                    3,

                  clampToGround:
                    true,

                  material:
                    Cesium.Color.fromCssColorString(
                      OTHER_TRAIL
                    ).withAlpha(
                      selectedTrail
                        ? 0.35
                        : 0.65
                    ),
                },
              });
            }
          );
        }
      );

      viewer.scene.requestRender();
    }

    drawOtherTrails().catch(
      console.error
    );
  }, [
    mapTrails,
    selectedTrail,
    viewerReady,
  ]);

  /*
   * ============================================================
   * SELECTED TRAIL
   * ============================================================
   */

  useEffect(() => {
    if (!viewerReady) {
      return;
    }

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
        await import(
          "cesium"
        );

      removeEntitiesByPrefix(
        "selected-trail-"
      );

      if (
        !selectedTrail
      ) {
        viewer.scene.requestRender();
        return;
      }

      const segments =
        geometryToSegments(
          selectedTrail.geometry
        );

      if (
        segments.length ===
        0
      ) {
        return;
      }

      /*
       * Draw route.
       */

      const allPositions: any[] =
        [];

      const allCoordinates:
        Coordinate[] = [];

      segments.forEach(
        (
          segment,
          segmentIndex
        ) => {
          const positions =
            segment.map(
              (
                coordinate
              ) =>
                Cesium.Cartesian3.fromDegrees(
                  coordinate[0],
                  coordinate[1],
                  0
                )
            );

          allPositions.push(
            ...positions
          );

          allCoordinates.push(
            ...segment
          );

          /*
           * Casing.
           */

          viewer.entities.add({
            id:
              `selected-trail-casing-${segmentIndex}`,

            name:
              "Selected trail",

            polyline: {
              positions,

              width:
                8,

              clampToGround:
                true,

              material:
                Cesium.Color.fromCssColorString(
                  TRAIL_CASING
                ).withAlpha(
                  0.88
                ),
            },
          });

          /*
           * Bright green route.
           */

          viewer.entities.add({
            id:
              `selected-trail-route-${segmentIndex}`,

            name:
              "Selected trail",

            polyline: {
              positions,

              width:
                5,

              clampToGround:
                true,

              material:
                Cesium.Color.fromCssColorString(
                  TRAIL_GREEN
                ),
            },
          });
        }
      );

      /*
       * ========================================================
       * START / END
       * ========================================================
       *
       * IMPORTANT:
       *
       * Use the backend's explicit endpoint coordinates.
       *
       * Do not derive them from arbitrary MultiLineString
       * ordering.
       */

      const backendStart =
        selectedTrail.start_coordinate;

      const backendEnd =
        selectedTrail.end_coordinate;

      if (
        selectedTrail.endpoint_available &&
        backendStart &&
        backendEnd
      ) {
        /*
         * Green start.
         */

        viewer.entities.add({
          id:
            "selected-trail-start",

          name:
            "Trail start",

          position:
            Cesium.Cartesian3.fromDegrees(
              backendStart[0],
              backendStart[1],
              0
            ),

          point: {
            pixelSize:
              14,

            color:
              Cesium.Color.fromCssColorString(
                START_GREEN
              ),

            heightReference:
              Cesium.HeightReference
                .CLAMP_TO_GROUND,

            disableDepthTestDistance:
              Number.POSITIVE_INFINITY,
          },
        });

        /*
         * Red end.
         */

        viewer.entities.add({
          id:
            "selected-trail-end",

          name:
            "Trail end",

          position:
            Cesium.Cartesian3.fromDegrees(
              backendEnd[0],
              backendEnd[1],
              0
            ),

          point: {
            pixelSize:
              14,

            color:
              Cesium.Color.fromCssColorString(
                END_RED
              ),

            heightReference:
              Cesium.HeightReference
                .CLAMP_TO_GROUND,

            disableDepthTestDistance:
              Number.POSITIVE_INFINITY,
          },
        });
      }

      /*
       * ========================================================
       * CAMERA BOUNDS
       * ========================================================
       */

      const bounds =
        calculateRouteBounds(
          allCoordinates
        );

      if (!bounds) {
        return;
      }

      /*
       * ========================================================
       * 2D CAMERA
       * ========================================================
       */

      if (
        viewState ===
        "map"
      ) {
        const canvas =
          viewer.canvas;

        const viewportWidth =
          Math.max(
            canvas.clientWidth,
            1
          );

        const viewportHeight =
          Math.max(
            canvas.clientHeight,
            1
          );

        const aspect =
          viewportWidth /
          viewportHeight;

        const latitudeRadians =
          bounds.centerLatitude *
          (
            Math.PI /
            180
          );

        const metersPerDegreeLatitude =
          111320;

        const metersPerDegreeLongitude =
          111320 *
          Math.max(
            Math.cos(
              latitudeRadians
            ),
            0.01
          );

        const longitudeSpan =
          Math.max(
            bounds.east -
              bounds.west,
            0.0005
          );

        const latitudeSpan =
          Math.max(
            bounds.north -
              bounds.south,
            0.0005
          );

        const routeWidthMeters =
          longitudeSpan *
          metersPerDegreeLongitude;

        const routeHeightMeters =
          latitudeSpan *
          metersPerDegreeLatitude;

        const requiredGroundExtent =
          Math.max(
            routeHeightMeters,
            routeWidthMeters /
              Math.max(
                aspect,
                0.5
              )
          );

        /*
         * Comfortable but not excessive.
         */

        const cameraHeight =
          Math.max(
            requiredGroundExtent *
              1.45,
            4500
          );

        viewer.camera.flyTo({
          destination:
            Cesium.Cartesian3.fromDegrees(
              bounds.centerLongitude,
              bounds.centerLatitude,
              cameraHeight
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
            1,
        });
      } else {
        /*
         * ======================================================
         * 3D CAMERA
         * ======================================================
         *
         * Do NOT use Cesium automatic range here.
         *
         * Calculate a controlled route-relative distance.
         */

        const routeSphere =
          Cesium.BoundingSphere.fromPoints(
            allPositions
          );

        /*
         * Route radius.
         */

        const radius =
          Math.max(
            routeSphere.radius,
            1200
          );

        /*
         * Controlled distance.
         *
         * This is deliberately conservative:
         *
         * not the old close-up,
         * not the previous excessive distant view.
         */

        const range =
          Math.max(
            radius *
              2.35,
            4500
          );

        viewer.camera.flyToBoundingSphere(
          routeSphere,
          {
            duration:
              1.15,

            offset:
              new Cesium.HeadingPitchRange(
                0,

                Cesium.Math.toRadians(
                  -58
                ),

                range
              ),
          }
        );
      }

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
    viewState,
  ]);

  /*
   * ============================================================
   * ZOOM
   * ============================================================
   */

  async function zoomIn() {
    const viewer =
      viewerRef.current;

    if (
      !viewer ||
      viewer.isDestroyed()
    ) {
      return;
    }

    const height =
      viewer.camera
        .positionCartographic
        .height;

    viewer.camera.zoomIn(
      Math.max(
        height *
          0.25,
        100
      )
    );

    viewer.scene.requestRender();
  }

  async function zoomOut() {
    const viewer =
      viewerRef.current;

    if (
      !viewer ||
      viewer.isDestroyed()
    ) {
      return;
    }

    const height =
      viewer.camera
        .positionCartographic
        .height;

    viewer.camera.zoomOut(
      Math.max(
        height *
          0.25,
        100
      )
    );

    viewer.scene.requestRender();
  }

  /*
   * ============================================================
   * NORTH
   * ============================================================
   */

  async function resetNorth() {
    const viewer =
      viewerRef.current;

    if (
      !viewer ||
      viewer.isDestroyed()
    ) {
      return;
    }

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

  /*
   * ============================================================
   * CENTER
   * ============================================================
   */

  async function centerLocation() {
    const currentLocation =
      location;

    if (!currentLocation) {
      return;
    }

    const viewer =
      viewerRef.current;

    if (
      !viewer ||
      viewer.isDestroyed()
    ) {
      return;
    }

    if (selectedTrail) {
      return;
    }

    const Cesium =
      await import(
        "cesium"
      );

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
                -50
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

  /*
   * ============================================================
   * 3D TERRAIN TOGGLE
   * ============================================================
   */

  async function toggle3DTerrain() {
    const viewer =
      viewerRef.current;

    if (
      !viewer ||
      viewer.isDestroyed()
    ) {
      return;
    }

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
      await import(
        "cesium"
      );

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

        const currentLocation =
          location;

        if (
          !selectedTrail &&
          currentLocation
        ) {
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
                  -50
                ),

              roll:
                0,
            },

            duration:
              0.8,
          });
        }
      } catch (
        error
      ) {
        console.error(
          "3D terrain activation failed:",
          error
        );
      }
    } else {
      setViewState(
        "map"
      );

      const currentLocation =
        location;

      if (
        !selectedTrail &&
        currentLocation
      ) {
        viewer.camera.flyTo({
          destination:
            Cesium.Cartesian3.fromDegrees(
              currentLocation.longitude,
              currentLocation.latitude,
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
            0.8,
        });
      }
    }

    viewer.scene.requestRender();
  }

  /*
   * ============================================================
   * UI
   * ============================================================
   */

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 h-full w-full overflow-hidden bg-[#d8dde2]"
    >
      <div className="absolute right-4 top-4 z-50 flex gap-2">
        <button
          type="button"
          onClick={() =>
            setLayersOpen(
              (
                open
              ) => !open
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
              ? "border-[#63E96B]/50 bg-[#63E96B]/15 text-[#e4ffe0]"
              : "border-white/20 bg-[#07111f]/90 text-white hover:bg-[#0b1929]",
          ].join(" ")}
        >
          {viewState ===
          "terrain"
            ? "Map view"
            : "3D terrain"}
        </button>
      </div>

      {layersOpen && (
        <div className="absolute right-4 top-[64px] z-50 w-[290px] rounded-2xl border border-white/10 bg-[#07111f]/95 p-4 shadow-2xl backdrop-blur-xl">
          <p className="text-[10px] font-medium uppercase tracking-[0.2em] text-white/40">
            Map layers
          </p>

          <div className="mt-4 space-y-2">
            <button
              type="button"
              disabled={
                !satelliteAvailable
              }
              onClick={() =>
                setBaseMap(
                  "satellite"
                )
              }
              className={[
                "flex w-full items-center justify-between rounded-xl px-3 py-3 text-left transition",
                baseMap ===
                "satellite"
                  ? "bg-white/[0.10]"
                  : "bg-white/[0.04] hover:bg-white/[0.07]",
                !satelliteAvailable
                  ? "cursor-not-allowed opacity-40"
                  : "",
              ].join(" ")}
            >
              <span className="text-xs text-white/85">
                Satellite
              </span>

              <span className="text-[9px] uppercase tracking-[0.12em] text-white/35">
                {satelliteAvailable
                  ? baseMap ===
                    "satellite"
                    ? "Active"
                    : "Available"
                  : "Unavailable"}
              </span>
            </button>

            <button
              type="button"
              onClick={() =>
                setBaseMap(
                  "osm"
                )
              }
              className={[
                "flex w-full items-center justify-between rounded-xl px-3 py-3 text-left transition",
                baseMap ===
                "osm"
                  ? "bg-white/[0.10]"
                  : "bg-white/[0.04] hover:bg-white/[0.07]",
              ].join(" ")}
            >
              <span className="text-xs text-white/85">
                OpenStreetMap
              </span>

              <span className="text-[9px] uppercase tracking-[0.12em] text-white/35">
                {baseMap ===
                "osm"
                  ? "Active"
                  : "Available"}
              </span>
            </button>
          </div>
        </div>
      )}

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
          aria-label="Center map"
          className="flex h-10 w-10 items-center justify-center text-lg text-white/70 transition hover:bg-white/10"
        >
          ◎
        </button>
      </div>

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

      <div className="absolute bottom-1 right-1 z-40 rounded bg-white/80 px-2 py-1 text-[9px] text-black/60 backdrop-blur-sm">
        © OpenStreetMap contributors
      </div>
    </div>
  );
}