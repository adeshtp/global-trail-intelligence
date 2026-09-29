"use client";

import { useEffect, useRef, useState } from "react";
import type {
  Cartesian3,
  ImageryLayer,
  Viewer,
} from "cesium";
import "cesium/Build/Cesium/Widgets/widgets.css";

type Location = {
  latitude: number;
  longitude: number;
};

type Coordinate = [number, number];

type Geometry = {
  type: "LineString" | "MultiLineString" | string;
  coordinates: number[][] | number[][][];
};

type MapTrail = {
  trail_id: string;
  osm_id: number | null;
  osm_type: "way" | "relation" | "component" | null;
  name: string | null;
  geometry: Geometry;
};

type TrailGeometry = {
  trail_id: string;
  osm_id: number | null;
  osm_type: "way" | "relation" | "component" | null;
  name: string | null;
  route_type: string | null;
  highway_type: string | null;
  description: string | null;
  difficulty: string | null;
  surface: string | null;
  trail_visibility: string | null;
  distance_km?: number | null;
  member_way_ids?: number[];
  geometry_hash?: string | null;
  geometry_provenance?: string | null;
  start_coordinate?: Coordinate | null;
  end_coordinate?: Coordinate | null;
  endpoint_available?: boolean | null;
  geometry: Geometry;
};

type CesiumMapProps = {
  location: Location | null;
  /**
   * The place name the Explore page already resolved from the search. It
   * is passed in rather than looked up again, so the map shows the real
   * searched place without a geocoding request of its own.
   */
  locationName?: string | null;
  mapTrails: MapTrail[];
  selectedTrail: TrailGeometry | null;
  expanded?: boolean;
  onExpand?: () => void;
  onCollapse?: () => void;
};

type ViewState = "map" | "terrain";
type BaseMap = "satellite" | "osm";

const SELECTED_BLUE = "#42B8FF";
const SELECTED_CASING = "#102A35";
const OTHER_TRAIL = "#D8EBDD";
const START_GREEN = "#22C55E";
const END_RED = "#EF4444";
const UI_BG = "#07111f";

function isValidCoordinate(value: unknown): value is Coordinate {
  if (!Array.isArray(value) || value.length < 2) return false;

  const longitude = Number(value[0]);
  const latitude = Number(value[1]);

  return (
    Number.isFinite(longitude) &&
    Number.isFinite(latitude) &&
    longitude >= -180 &&
    longitude <= 180 &&
    latitude >= -90 &&
    latitude <= 90
  );
}

function getSegments(geometry: Geometry): number[][][] {
  if (geometry.type === "LineString") {
    return [geometry.coordinates as number[][]];
  }

  if (geometry.type === "MultiLineString") {
    return geometry.coordinates as number[][][];
  }

  return [];
}

function getValidCoordinates(geometry: Geometry): Coordinate[] {
  const coordinates: Coordinate[] = [];

  getSegments(geometry).forEach((segment) => {
    segment.forEach((coordinate) => {
      if (isValidCoordinate(coordinate)) {
        coordinates.push([
          Number(coordinate[0]),
          Number(coordinate[1]),
        ]);
      }
    });
  });

  return coordinates;
}

function getBounds(coordinates: Coordinate[]) {
  if (coordinates.length === 0) return null;

  let west = Number.POSITIVE_INFINITY;
  let east = Number.NEGATIVE_INFINITY;
  let south = Number.POSITIVE_INFINITY;
  let north = Number.NEGATIVE_INFINITY;

  coordinates.forEach(([longitude, latitude]) => {
    west = Math.min(west, longitude);
    east = Math.max(east, longitude);
    south = Math.min(south, latitude);
    north = Math.max(north, latitude);
  });

  return {
    west,
    east,
    south,
    north,
    centerLongitude: (west + east) / 2,
    centerLatitude: (south + north) / 2,
  };
}

export default function CesiumMap({
  location,
  locationName = null,
  mapTrails,
  selectedTrail,
  expanded = false,
  onExpand,
  onCollapse,
}: CesiumMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<Viewer | null>(null);

  const [viewerReady, setViewerReady] = useState(false);
  const [layersOpen, setLayersOpen] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [viewState, setViewState] = useState<ViewState>("map");
  const [baseMap, setBaseMap] = useState<BaseMap>("satellite");
  const [satelliteAvailable, setSatelliteAvailable] = useState(false);

  const osmLayerRef = useRef<ImageryLayer | null>(null);
  const satelliteLayerRef = useRef<ImageryLayer | null>(null);
  const locationRequestRef = useRef(0);
  const mapTrailRequestRef = useRef(0);
  const selectedTrailRequestRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    async function initialize() {
      if (!containerRef.current || viewerRef.current) return;

      (window as typeof window & { CESIUM_BASE_URL: string }).CESIUM_BASE_URL =
        "/cesium/";

      const Cesium = await import("cesium");

      if (cancelled || !containerRef.current) return;

      const token = process.env.NEXT_PUBLIC_CESIUM_ION_TOKEN;

      if (token) {
        Cesium.Ion.defaultAccessToken = token;
      }

      const viewer = new Cesium.Viewer(containerRef.current, {
        terrain: token ? Cesium.Terrain.fromWorldTerrain() : undefined,
        animation: false,
        timeline: false,
        baseLayerPicker: false,
        geocoder: false,
        homeButton: false,
        sceneModePicker: false,
        navigationHelpButton: false,
        fullscreenButton: false,
        infoBox: false,
        selectionIndicator: false,
        shadows: false,
        requestRenderMode: true,
        maximumRenderTimeChange: Infinity,
      });

      viewerRef.current = viewer;

      const bottomContainer = viewer.bottomContainer as HTMLElement | undefined;
      if (bottomContainer) bottomContainer.style.display = "none";

      const osmProvider = new Cesium.UrlTemplateImageryProvider({
        url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        credit: "© OpenStreetMap contributors",
        maximumLevel: 19,
      });

      viewer.imageryLayers.removeAll();

      const osmLayer = viewer.imageryLayers.addImageryProvider(osmProvider);
      osmLayerRef.current = osmLayer;

      if (token) {
        try {
          const satelliteProvider = await Cesium.createWorldImageryAsync({
            style: Cesium.IonWorldImageryStyle.AERIAL,
          });

          if (cancelled || viewer.isDestroyed()) return;

          const satelliteLayer =
            viewer.imageryLayers.addImageryProvider(satelliteProvider);

          satelliteLayerRef.current = satelliteLayer;
          satelliteLayer.show = true;
          osmLayer.show = false;
          setSatelliteAvailable(true);
          setBaseMap("satellite");
        } catch (error) {
          console.error("Satellite imagery initialization failed:", error);
          osmLayer.show = true;
          setSatelliteAvailable(false);
          setBaseMap("osm");
        }
      } else {
        osmLayer.show = true;
        setSatelliteAvailable(false);
        setBaseMap("osm");
      }

      const controller = viewer.scene.screenSpaceCameraController;
      controller.inertiaSpin = 0;
      controller.inertiaTranslate = 0.01;
      controller.inertiaZoom = 0.02;
      controller.minimumZoomDistance = 40;
      controller.maximumZoomDistance = 50000000;
      controller.enableCollisionDetection = true;
      controller.enableInputs = true;

      viewer.scene.globe.show = true;
      viewer.scene.globe.enableLighting = false;
      viewer.scene.globe.depthTestAgainstTerrain = true;

      viewer.camera.setView({
        destination: Cesium.Cartesian3.fromDegrees(0, 20, 18000000),
        orientation: {
          heading: 0,
          pitch: Cesium.Math.toRadians(-90),
          roll: 0,
        },
      });

      viewer.resize();
      viewer.scene.requestRender();

      if (!cancelled) setViewerReady(true);
    }

    initialize().catch((error) => {
      console.error("Cesium initialization failed:", error);
    });

    return () => {
      cancelled = true;

      const viewer = viewerRef.current;
      if (viewer && !viewer.isDestroyed()) viewer.destroy();

      viewerRef.current = null;
      setViewerReady(false);
    };
  }, []);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;

    const observer = new ResizeObserver(() => {
      const viewer = viewerRef.current;
      if (viewer && !viewer.isDestroyed()) {
        viewer.resize();
        viewer.scene.requestRender();
      }
    });

    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!viewerReady || !location) return;

    const requestId = ++locationRequestRef.current;
    const currentLocation = location;
    // The real searched-place name, passed in by the page. No geocoding
    // request is made here; a name is only drawn when one was resolved.
    const currentName = locationName?.trim() || null;

    async function moveToLocation() {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;

      const Cesium = await import("cesium");
      if (requestId !== locationRequestRef.current) return;

      const latitude = Number(currentLocation.latitude);
      const longitude = Number(currentLocation.longitude);

      if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return;

      const oldMarker = viewer.entities.getById("search-location");
      if (oldMarker) viewer.entities.remove(oldMarker);

      viewer.entities.add({
        id: "search-location",
        name: currentName ?? "Searched location",
        position: Cesium.Cartesian3.fromDegrees(longitude, latitude, 0),
        point: {
          pixelSize: 10,
          color: Cesium.Color.fromCssColorString("#F8FAFC"),
          outlineColor: Cesium.Color.fromCssColorString("#22D3EE"),
          outlineWidth: 3,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        label: currentName
          ? {
              text: currentName,
              font: "12px sans-serif",
              fillColor: Cesium.Color.WHITE,
              showBackground: true,
              backgroundColor: Cesium.Color.fromCssColorString(
                UI_BG
              ).withAlpha(0.9),
              backgroundPadding: new Cesium.Cartesian2(8, 5),
              heightReference:
                Cesium.HeightReference.CLAMP_TO_GROUND,
              pixelOffset: new Cesium.Cartesian2(0, -26),
              disableDepthTestDistance: Number.POSITIVE_INFINITY,
            }
          : undefined,
      });

      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(longitude, latitude, 10000),
        orientation: {
          heading: 0,
          pitch: Cesium.Math.toRadians(-90),
          roll: 0,
        },
        duration: 1,
      });

      setViewState("map");
    }

    moveToLocation().catch((error) => {
      console.error("Location camera movement failed:", error);
    });
  }, [location, locationName, viewerReady]);

  useEffect(() => {
    if (!viewerReady) return;

    let cancelled = false;
    const requestId = ++mapTrailRequestRef.current;

    async function drawMapTrails() {
      const Cesium = await import("cesium");
      if (
        cancelled ||
        requestId !== mapTrailRequestRef.current
      ) {
        return;
      }

      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;

      viewer.entities.values.slice().forEach((entity) => {
        if (
          typeof entity.id === "string" &&
          entity.id.startsWith("discovered-trail-")
        ) {
          viewer.entities.remove(entity);
        }
      });

      mapTrails.forEach((trail, trailIndex) => {
        if (!trail.geometry) return;

        getSegments(trail.geometry).forEach((segment, segmentIndex) => {
          const positions = segment
            .filter(isValidCoordinate)
            .map((coordinate) =>
              Cesium.Cartesian3.fromDegrees(
                Number(coordinate[0]),
                Number(coordinate[1]),
                0
              )
            );

          if (positions.length < 2) return;

          viewer.entities.add({
            id: `discovered-trail-${trail.trail_id}-${trailIndex}-${segmentIndex}`,
            name: trail.name ?? "Available trail",
            polyline: {
              positions,
              width: 3,
              clampToGround: true,
              material: Cesium.Color.fromCssColorString(OTHER_TRAIL).withAlpha(
                selectedTrail ? 0.38 : 0.72
              ),
            },
          });
        });
      });

      viewer.scene.requestRender();
    }

    void drawMapTrails().catch((error) => {
      if (!cancelled) {
        console.error("Trail map rendering failed:", error);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [mapTrails, selectedTrail, viewerReady]);

  useEffect(() => {
    if (!viewerReady) return;

    let cancelled = false;
    const requestId = ++selectedTrailRequestRef.current;

    async function drawSelectedTrail() {
      const Cesium = await import("cesium");
      if (
        cancelled ||
        requestId !== selectedTrailRequestRef.current
      ) {
        return;
      }

      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;

      viewer.entities.values.slice().forEach((entity) => {
        if (
          typeof entity.id === "string" &&
          entity.id.startsWith("selected-trail-")
        ) {
          viewer.entities.remove(entity);
        }
      });

      if (!selectedTrail) {
        viewer.scene.requestRender();
        return;
      }

      const segments = getSegments(selectedTrail.geometry);
      const allPositions: Cartesian3[] = [];
      const allCoordinates: Coordinate[] = [];

      segments.forEach((segment, segmentIndex) => {
        const validCoordinates = segment.filter(isValidCoordinate).map((coordinate) => [
          Number(coordinate[0]),
          Number(coordinate[1]),
        ] as Coordinate);

        if (validCoordinates.length < 2) return;

        allCoordinates.push(...validCoordinates);

        const positions = validCoordinates.map(([longitude, latitude]) =>
          Cesium.Cartesian3.fromDegrees(longitude, latitude, 0)
        );

        allPositions.push(...positions);

        viewer.entities.add({
          id: `selected-trail-casing-${selectedTrail.trail_id}-${segmentIndex}`,
          name: selectedTrail.name ?? "Selected trail",
          polyline: {
            positions,
            width: expanded ? 9 : 8,
            clampToGround: true,
            zIndex: 1,
            material: Cesium.Color.fromCssColorString(SELECTED_CASING).withAlpha(
              0.95
            ),
            depthFailMaterial: new Cesium.ColorMaterialProperty(
              Cesium.Color.fromCssColorString(SELECTED_CASING).withAlpha(0.9)
            ),
          },
        });

        viewer.entities.add({
          id: `selected-trail-route-${selectedTrail.trail_id}-${segmentIndex}`,
          name: selectedTrail.name ?? "Selected trail",
          polyline: {
            positions,
            width: expanded ? 5 : 5,
            clampToGround: true,
            zIndex: 2,
            material: Cesium.Color.fromCssColorString(SELECTED_BLUE),
            depthFailMaterial: new Cesium.ColorMaterialProperty(
              Cesium.Color.fromCssColorString(SELECTED_BLUE)
            ),
          },
        });
      });

      if (allPositions.length < 2 || allCoordinates.length < 2) {
        viewer.scene.requestRender();
        return;
      }

      const explicitStart = selectedTrail.start_coordinate;
      const explicitEnd = selectedTrail.end_coordinate;
      const hasVerifiedEndpoints =
        selectedTrail.endpoint_available === true &&
        isValidCoordinate(explicitStart) &&
        isValidCoordinate(explicitEnd);

      if (hasVerifiedEndpoints) {
        viewer.entities.add({
          id: `selected-trail-start-${selectedTrail.trail_id}`,
          name: "Trail start",
          position: Cesium.Cartesian3.fromDegrees(
            explicitStart[0],
            explicitStart[1],
            0
          ),
          point: {
            pixelSize: 15,
            color: Cesium.Color.fromCssColorString(START_GREEN),
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 3,
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
          label: {
            text: "Start",
            font: "12px sans-serif",
            fillColor: Cesium.Color.WHITE,
            showBackground: true,
            backgroundColor: Cesium.Color.fromCssColorString(UI_BG).withAlpha(0.9),
            backgroundPadding: new Cesium.Cartesian2(8, 5),
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            pixelOffset: new Cesium.Cartesian2(0, -24),
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        });

        viewer.entities.add({
          id: `selected-trail-end-${selectedTrail.trail_id}`,
          name: "Trail end",
          position: Cesium.Cartesian3.fromDegrees(
            explicitEnd[0],
            explicitEnd[1],
            0
          ),
          point: {
            pixelSize: 15,
            color: Cesium.Color.fromCssColorString(END_RED),
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 3,
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
          label: {
            text: "End",
            font: "12px sans-serif",
            fillColor: Cesium.Color.WHITE,
            showBackground: true,
            backgroundColor: Cesium.Color.fromCssColorString(UI_BG).withAlpha(0.9),
            backgroundPadding: new Cesium.Cartesian2(8, 5),
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            pixelOffset: new Cesium.Cartesian2(0, -24),
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        });
      }

      const bounds = getBounds(allCoordinates);
      if (!bounds) return;

      if (viewState === "map") {
        const canvas = viewer.canvas;
        const viewportWidth = Math.max(canvas.clientWidth, 1);
        const viewportHeight = Math.max(canvas.clientHeight, 1);
        const aspect = viewportWidth / viewportHeight;

        const latitudeRadians = bounds.centerLatitude * (Math.PI / 180);
        const metersPerDegreeLatitude = 111320;
        const metersPerDegreeLongitude =
          111320 * Math.max(Math.cos(latitudeRadians), 0.01);

        const longitudeSpan = Math.max(bounds.east - bounds.west, 0.0005);
        const latitudeSpan = Math.max(bounds.north - bounds.south, 0.0005);

        const routeWidthMeters =
          longitudeSpan * metersPerDegreeLongitude;
        const routeHeightMeters =
          latitudeSpan * metersPerDegreeLatitude;

        const requiredGroundExtent = Math.max(
          routeHeightMeters,
          routeWidthMeters / Math.max(aspect, 0.5)
        );

        const cameraHeight = Math.max(
          requiredGroundExtent * 1.45,
          4500
        );

        viewer.camera.cancelFlight();

        viewer.camera.flyTo({
          destination: Cesium.Cartesian3.fromDegrees(
            bounds.centerLongitude,
            bounds.centerLatitude,
            cameraHeight
          ),
          orientation: {
            heading: 0,
            pitch: Cesium.Math.toRadians(-90),
            roll: 0,
          },
          duration: 1,
        });
      } else {
        const routeSphere = Cesium.BoundingSphere.fromPoints(allPositions);
        const radius = Math.max(routeSphere.radius, 1200);
        const range = Math.max(radius * 2.35, 4500);

        viewer.camera.flyToBoundingSphere(routeSphere, {
          duration: 1.15,
          offset: new Cesium.HeadingPitchRange(
            0,
            Cesium.Math.toRadians(-58),
            range
          ),
        });
      }

      viewer.scene.requestRender();
    }

    void drawSelectedTrail().catch((error) => {
      if (!cancelled) {
        console.error("Selected trail rendering failed:", error);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [selectedTrail, viewerReady, expanded, viewState]);

  async function zoomIn() {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    await import("cesium");

    const height = viewer.camera.positionCartographic.height;
    viewer.camera.zoomIn(Math.max(height * 0.25, 100));
    viewer.scene.requestRender();
  }

  async function zoomOut() {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    await import("cesium");

    const height = viewer.camera.positionCartographic.height;
    viewer.camera.zoomOut(Math.max(height * 0.25, 100));
    viewer.scene.requestRender();
  }

  async function resetNorth() {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    await import("cesium");

    viewer.camera.setView({
      orientation: {
        heading: 0,
        pitch: viewer.camera.pitch,
        roll: 0,
      },
    });

    viewer.scene.requestRender();
  }

  async function centerLocation() {
    if (!location) return;

    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    const Cesium = await import("cesium");

    viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(
        location.longitude,
        location.latitude,
        viewState === "terrain" ? 11000 : 9000
      ),
      orientation: {
        heading: 0,
        pitch:
          viewState === "terrain"
            ? Cesium.Math.toRadians(-50)
            : Cesium.Math.toRadians(-90),
        roll: 0,
      },
      duration: 0.8,
    });
  }

  async function changeBaseMap(nextMap: BaseMap) {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    let satelliteLayer = satelliteLayerRef.current;

    if (nextMap === "satellite" && !satelliteLayer) {
      const token = process.env.NEXT_PUBLIC_CESIUM_ION_TOKEN;

      if (!token) {
        console.error(
          "NEXT_PUBLIC_CESIUM_ION_TOKEN is missing; satellite imagery is unavailable."
        );
        return;
      }

      const Cesium = await import("cesium");

      try {
        const satelliteProvider = await Cesium.createWorldImageryAsync({
          style: Cesium.IonWorldImageryStyle.AERIAL,
        });

        satelliteLayer =
          viewer.imageryLayers.addImageryProvider(satelliteProvider);

        satelliteLayerRef.current = satelliteLayer;
        setSatelliteAvailable(true);
      } catch (error) {
        console.error("Satellite imagery loading failed:", error);
        return;
      }
    }

    const osmLayer = osmLayerRef.current;

    if (osmLayer) {
      osmLayer.show = nextMap === "osm";
    }

    if (satelliteLayer) {
      // Cesium layers are imperative external objects, not React state.
      // eslint-disable-next-line react-hooks/immutability
      satelliteLayer.show = nextMap === "satellite";
    }

    setBaseMap(nextMap);
    viewer.scene.requestRender();
  }

  async function rotateCamera(degrees: number) {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    const Cesium = await import("cesium");

    viewer.camera.setView({
      orientation: {
        heading:
          viewer.camera.heading + Cesium.Math.toRadians(degrees),
        pitch: viewer.camera.pitch,
        roll: 0,
      },
    });

    viewer.scene.requestRender();
  }

  async function adjustPitch(degrees: number) {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    const Cesium = await import("cesium");

    const nextPitch = Math.max(
      Cesium.Math.toRadians(-88),
      Math.min(
        Cesium.Math.toRadians(-18),
        viewer.camera.pitch + Cesium.Math.toRadians(degrees)
      )
    );

    viewer.camera.setView({
      orientation: {
        heading: viewer.camera.heading,
        pitch: nextPitch,
        roll: 0,
      },
    });

    viewer.scene.requestRender();
  }

  async function topDownView() {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    const Cesium = await import("cesium");

    viewer.camera.setView({
      orientation: {
        heading: 0,
        pitch: Cesium.Math.toRadians(-90),
        roll: 0,
      },
    });

    viewer.scene.requestRender();
  }

  async function obliqueView() {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    const Cesium = await import("cesium");

    viewer.camera.setView({
      orientation: {
        heading: viewer.camera.heading,
        pitch: Cesium.Math.toRadians(-52),
        roll: 0,
      },
    });

    viewer.scene.requestRender();
  }

  async function centerSelectedTrail() {
    if (!selectedTrail) return;

    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    const Cesium = await import("cesium");
    const coordinates = getValidCoordinates(selectedTrail.geometry);

    if (coordinates.length < 2) return;

    if (viewState === "map") {
      const bounds = getBounds(coordinates);
      if (!bounds) return;

      const canvas = viewer.canvas;
      const width = Math.max(canvas.clientWidth, 1);
      const height = Math.max(canvas.clientHeight, 1);
      const aspect = width / height;

      const latitudeMeters =
        Math.max(bounds.north - bounds.south, 0.0005) * 111320;
      const longitudeMeters =
        Math.max(bounds.east - bounds.west, 0.0005) *
        111320 *
        Math.max(Math.cos(Cesium.Math.toRadians(bounds.centerLatitude)), 0.01);

      const extent = Math.max(
        latitudeMeters,
        longitudeMeters / Math.max(aspect, 0.5)
      );

      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(
          bounds.centerLongitude,
          bounds.centerLatitude,
          Math.max(extent * 1.45, 4500)
        ),
        orientation: {
          heading: 0,
          pitch: Cesium.Math.toRadians(-90),
          roll: 0,
        },
        duration: 0.8,
      });
    } else {
      const positions = coordinates.map(([longitude, latitude]) =>
        Cesium.Cartesian3.fromDegrees(longitude, latitude, 0)
      );

      const sphere = Cesium.BoundingSphere.fromPoints(positions);
      const radius = Math.max(sphere.radius, 1200);
      const range = Math.max(radius * 2.35, 4500);

      viewer.camera.flyToBoundingSphere(sphere, {
        duration: 0.8,
        offset: new Cesium.HeadingPitchRange(
          0,
          Cesium.Math.toRadians(-58),
          range
        ),
      });
    }
  }

  async function toggle3DTerrain() {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    const token = process.env.NEXT_PUBLIC_CESIUM_ION_TOKEN;
    if (!token) {
      console.error("NEXT_PUBLIC_CESIUM_ION_TOKEN is missing.");
      return;
    }

    const Cesium = await import("cesium");

    if (viewState === "map") {
      try {
        if (viewer.scene.mode !== Cesium.SceneMode.SCENE3D) {
          viewer.scene.morphTo3D(0.7);
        }

        viewer.terrainProvider = await Cesium.createWorldTerrainAsync({});
        setViewState("terrain");
      } catch (error) {
        console.error("3D terrain activation failed:", error);
      }
    } else {
      viewer.scene.morphTo2D(0.7);
      setViewState("map");
    }

    viewer.scene.requestRender();
  }

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 h-full w-full overflow-hidden bg-[#d8dde2]"
    >
      {selectedTrail && !expanded ? (
        <div className="absolute left-4 top-4 z-40 max-w-[280px] rounded-2xl border border-sky-200/20 bg-[#07111f]/90 px-4 py-3 shadow-2xl backdrop-blur-xl">
          <p className="text-[9px] font-medium uppercase tracking-[0.18em] text-sky-200/75">
            Selected trail
          </p>
          <p className="mt-1 truncate text-sm font-semibold text-white">
            {selectedTrail.name ?? "Selected trail"}
          </p>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-white/45">
            <span>
              {typeof selectedTrail.distance_km === "number" &&
              Number.isFinite(selectedTrail.distance_km)
                ? `${selectedTrail.distance_km.toFixed(1)} km`
                : "Distance unavailable"}
            </span>
            {selectedTrail.route_type ? (
              <span>{selectedTrail.route_type}</span>
            ) : null}
          </div>
        </div>
      ) : null}

      {/*
        Map controls are grouped so the map surface stays readable: one
        small 2x2 cluster instead of a row of unrelated floating buttons.
        Every popover closes the others, so two panels can never stack on
        top of each other over the route.
      */}
      <div className="absolute right-3 top-3 z-50 grid w-[176px] grid-cols-2 gap-1.5">
        <button
          type="button"
          onClick={() => {
            setLayersOpen((open) => !open);
            setCameraOpen(false);
            setDetailsOpen(false);
          }}
          aria-expanded={layersOpen}
          className={[
            "rounded-xl border px-2 py-2.5 text-[11px] font-semibold shadow-xl backdrop-blur-xl transition",
            layersOpen
              ? "border-sky-300/50 bg-sky-300/15 text-sky-100"
              : "border-white/20 bg-[#07111f]/90 text-white hover:bg-[#0b1929]",
          ].join(" ")}
        >
          Layers
        </button>

        <button
          type="button"
          onClick={() => {
            setDetailsOpen((open) => !open);
            setLayersOpen(false);
            setCameraOpen(false);
          }}
          aria-expanded={detailsOpen}
          className={[
            "rounded-xl border px-2 py-2.5 text-[11px] font-semibold shadow-xl backdrop-blur-xl transition",
            detailsOpen
              ? "border-sky-300/50 bg-sky-300/15 text-sky-100"
              : "border-white/20 bg-[#07111f]/90 text-white hover:bg-[#0b1929]",
          ].join(" ")}
        >
          Details
        </button>

        <button
          type="button"
          onClick={() => {
            setCameraOpen((open) => !open);
            setLayersOpen(false);
            setDetailsOpen(false);
          }}
          aria-expanded={cameraOpen}
          className={[
            "rounded-xl border px-2 py-2.5 text-[11px] font-semibold shadow-xl backdrop-blur-xl transition",
            cameraOpen
              ? "border-sky-300/50 bg-sky-300/15 text-sky-100"
              : "border-white/20 bg-[#07111f]/90 text-white hover:bg-[#0b1929]",
          ].join(" ")}
        >
          Camera
        </button>

        <button
          type="button"
          onClick={toggle3DTerrain}
          className={[
            "rounded-xl border px-2 py-2.5 text-[11px] font-semibold shadow-xl backdrop-blur-xl transition",
            viewState === "terrain"
              ? "border-[#63E96B]/50 bg-[#63E96B]/15 text-[#e4ffe0]"
              : "border-white/20 bg-[#07111f]/90 text-white hover:bg-[#0b1929]",
          ].join(" ")}
        >
          {viewState === "terrain" ? "Map view" : "3D terrain"}
        </button>
      </div>

      {/*
        Details shows only what the application already resolved: the
        searched place, the selected trail's own attributes, and the
        map/verification status it carries. Nothing is calculated here,
        and no intelligence (elevation, weather, difficulty) is invented
        to fill the panel.
      */}
      {detailsOpen ? (
        <div className="absolute right-3 top-[104px] z-50 max-w-[260px] rounded-2xl border border-white/10 bg-[#07111f]/95 p-4 shadow-2xl backdrop-blur-xl">
          <p className="text-[10px] font-medium uppercase tracking-[0.2em] text-white/40">
            Details
          </p>

          <p className="mt-3 text-[10px] uppercase tracking-[0.12em] text-white/25">
            Searched location
          </p>
          <p className="mt-1 text-xs leading-5 text-white/80">
            {locationName?.trim()
              ? locationName.trim()
              : "No place resolved yet"}
          </p>

          {selectedTrail ? (
            <>
              <p className="mt-4 text-[10px] uppercase tracking-[0.12em] text-white/25">
                Selected trail
              </p>
              <p className="mt-1 text-xs font-semibold leading-5 text-white">
                {selectedTrail.name?.trim() || "Unnamed trail"}
              </p>

              <dl className="mt-2 space-y-1">
                {(
                  [
                    [
                      "Trail type",
                      selectedTrail.route_type ??
                        selectedTrail.highway_type ??
                        selectedTrail.osm_type,
                    ],
                    [
                      "Map status",
                      selectedTrail.endpoint_available === true
                        ? "Verified geometry with start and end"
                        : "Verified geometry",
                    ],
                    [
                      "Distance",
                      typeof selectedTrail.distance_km ===
                        "number" &&
                      Number.isFinite(selectedTrail.distance_km)
                        ? `${selectedTrail.distance_km.toFixed(1)} km`
                        : "Not available",
                    ],
                    [
                      "Route sections",
                      Array.isArray(
                        selectedTrail.member_way_ids
                      )
                        ? `${selectedTrail.member_way_ids.length} mapped section${
                            selectedTrail.member_way_ids.length === 1
                              ? ""
                              : "s"
                          }`
                        : "Not reported",
                    ],
                  ] as const
                )
                  .filter(
                    ([, value]) =>
                      typeof value === "string" &&
                      value.length > 0
                  )
                  .map(([label, value]) => (
                    <div
                      key={label}
                      className="flex items-baseline justify-between gap-2 text-[11px]"
                    >
                      <dt className="text-white/35">{label}</dt>
                      <dd className="truncate text-right text-white/70">
                        {value}
                      </dd>
                    </div>
                  ))}
              </dl>
            </>
          ) : (
            <p className="mt-4 text-[11px] leading-5 text-white/35">
              Select a trail to see its route details.
            </p>
          )}
        </div>
      ) : null}

      {layersOpen ? (
        <div className="absolute right-3 top-[104px] z-50 w-[260px] rounded-2xl border border-white/10 bg-[#07111f]/95 p-4 shadow-2xl backdrop-blur-xl">
          <p className="text-[10px] font-medium uppercase tracking-[0.2em] text-white/40">
            Map layers
          </p>

          <div className="mt-4 space-y-2">
            <button
              type="button"
              disabled={!satelliteAvailable}
              onClick={() => void changeBaseMap("satellite")}
              className={[
                "flex w-full items-center justify-between rounded-xl px-3 py-3 text-left transition",
                baseMap === "satellite"
                  ? "bg-white/[0.10]"
                  : "bg-white/[0.04] hover:bg-white/[0.07]",
                !satelliteAvailable
                  ? "cursor-not-allowed opacity-40"
                  : "",
              ].join(" ")}
            >
              <span className="text-xs text-white/85">Satellite</span>
              <span className="text-[9px] uppercase tracking-[0.12em] text-white/35">
                {satelliteAvailable
                  ? baseMap === "satellite"
                    ? "Active"
                    : "Available"
                  : "Unavailable"}
              </span>
            </button>

            <button
              type="button"
              onClick={() => void changeBaseMap("osm")}
              className={[
                "flex w-full items-center justify-between rounded-xl px-3 py-3 text-left transition",
                baseMap === "osm"
                  ? "bg-white/[0.10]"
                  : "bg-white/[0.04] hover:bg-white/[0.07]",
              ].join(" ")}
            >
              <span className="text-xs text-white/85">OpenStreetMap</span>
              <span className="text-[9px] uppercase tracking-[0.12em] text-white/35">
                {baseMap === "osm" ? "Active" : "Available"}
              </span>
            </button>
          </div>
        </div>
      ) : null}

      {cameraOpen ? (
        <div className="absolute right-3 top-[104px] z-50 w-[280px] rounded-2xl border border-white/10 bg-[#07111f]/95 p-4 shadow-2xl backdrop-blur-xl">
          <p className="text-[10px] font-medium uppercase tracking-[0.2em] text-white/40">
            Camera controls
          </p>
          <p className="mt-1 text-xs text-white/35">
            Rotate, tilt, zoom and frame the trail
          </p>

          <div className="mt-4 grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => void topDownView()}
              className="rounded-xl border border-white/[0.08] bg-white/[0.035] px-3 py-2.5 text-xs font-semibold text-white/75 transition hover:bg-white/[0.08] hover:text-white"
            >
              Top down
            </button>
            <button
              type="button"
              onClick={() => void obliqueView()}
              className="rounded-xl border border-white/[0.08] bg-white/[0.035] px-3 py-2.5 text-xs font-semibold text-white/75 transition hover:bg-white/[0.08] hover:text-white"
            >
              Oblique
            </button>
          </div>

          <div className="mt-2 grid grid-cols-4 gap-2">
            <button
              type="button"
              onClick={() => void rotateCamera(-15)}
              aria-label="Rotate camera left"
              className="rounded-xl border border-white/[0.08] bg-white/[0.035] py-2.5 text-sm text-white/75 transition hover:bg-white/[0.08] hover:text-white"
            >
              ↺
            </button>
            <button
              type="button"
              onClick={() => void rotateCamera(15)}
              aria-label="Rotate camera right"
              className="rounded-xl border border-white/[0.08] bg-white/[0.035] py-2.5 text-sm text-white/75 transition hover:bg-white/[0.08] hover:text-white"
            >
              ↻
            </button>
            <button
              type="button"
              onClick={() => void adjustPitch(8)}
              aria-label="Tilt camera upward"
              className="rounded-xl border border-white/[0.08] bg-white/[0.035] py-2.5 text-sm text-white/75 transition hover:bg-white/[0.08] hover:text-white"
            >
              ↑
            </button>
            <button
              type="button"
              onClick={() => void adjustPitch(-8)}
              aria-label="Tilt camera downward"
              className="rounded-xl border border-white/[0.08] bg-white/[0.035] py-2.5 text-sm text-white/75 transition hover:bg-white/[0.08] hover:text-white"
            >
              ↓
            </button>
          </div>

          <div className="mt-2 grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={zoomIn}
              className="rounded-xl border border-white/[0.08] bg-white/[0.035] px-3 py-2.5 text-xs font-semibold text-white/75 transition hover:bg-white/[0.08] hover:text-white"
            >
              Zoom in
            </button>
            <button
              type="button"
              onClick={zoomOut}
              className="rounded-xl border border-white/[0.08] bg-white/[0.035] px-3 py-2.5 text-xs font-semibold text-white/75 transition hover:bg-white/[0.08] hover:text-white"
            >
              Zoom out
            </button>
          </div>

          <button
            type="button"
            onClick={() => void centerSelectedTrail()}
            disabled={!selectedTrail}
            className="mt-2 w-full rounded-xl border border-sky-300/20 bg-sky-300/[0.06] px-3 py-2.5 text-xs font-semibold text-sky-100 transition hover:bg-sky-300/[0.11] disabled:cursor-not-allowed disabled:opacity-30"
          >
            Center selected trail
          </button>

          <button
            type="button"
            onClick={resetNorth}
            className="mt-2 w-full rounded-xl border border-white/[0.08] bg-white/[0.035] px-3 py-2.5 text-xs font-semibold text-white/70 transition hover:bg-white/[0.08] hover:text-white"
          >
            North up
          </button>
        </div>
      ) : null}

      <div className="absolute bottom-4 left-4 z-50 flex flex-col overflow-hidden rounded-2xl border border-white/15 bg-[#07111f]/90 shadow-2xl backdrop-blur-xl">
        <button
          type="button"
          onClick={zoomIn}
          aria-label="Zoom in"
          className="flex h-10 w-10 items-center justify-center border-b border-white/10 text-lg text-white/80 transition hover:bg-white/10"
        >
          +
        </button>
        <button
          type="button"
          onClick={zoomOut}
          aria-label="Zoom out"
          className="flex h-10 w-10 items-center justify-center border-b border-white/10 text-lg text-white/80 transition hover:bg-white/10"
        >
          −
        </button>
        <button
          type="button"
          onClick={resetNorth}
          aria-label="Reset north"
          className="flex h-10 w-10 items-center justify-center border-b border-white/10 text-[11px] font-semibold text-white/80 transition hover:bg-white/10"
        >
          N
        </button>
        <button
          type="button"
          onClick={centerLocation}
          aria-label="Center searched location"
          className="flex h-10 w-10 items-center justify-center text-lg text-white/75 transition hover:bg-white/10"
        >
          ◎
        </button>
      </div>

      {!expanded && onExpand ? (
        <button
          type="button"
          onClick={onExpand}
          className="absolute bottom-4 right-4 z-50 rounded-xl border border-white/20 bg-[#07111f]/90 px-4 py-2.5 text-xs font-medium text-white shadow-xl backdrop-blur-xl transition hover:bg-[#0b1929]"
        >
          Expand map ↗
        </button>
      ) : null}

      {expanded && onCollapse ? (
        <button
          type="button"
          onClick={onCollapse}
          className="absolute left-4 top-4 z-50 rounded-xl border border-white/20 bg-[#07111f]/90 px-4 py-2.5 text-xs font-medium text-white shadow-xl backdrop-blur-xl transition hover:bg-[#0b1929]"
        >
          Close
        </button>
      ) : null}

      {/*
        The map's own attribution. OpenStreetMap's tile usage policy requires
        the credit to be visible on the map, which is also where a reader is
        actually looking at the basemap. Cesium's own credit container is
        hidden above, so this replaces it rather than duplicating it, and the
        text is always shown rather than only on hover.
      */}
      <a
        href={
          baseMap === "osm"
            ? "https://www.openstreetmap.org/copyright"
            : "https://cesium.com/platform/terms/"
        }
        target="_blank"
        rel="noreferrer"
        className="absolute bottom-1 right-1 z-40 rounded bg-white/80 px-2 py-1 text-[9px] text-black/60 underline decoration-black/20 underline-offset-1 backdrop-blur-sm hover:text-black/80"
      >
        {baseMap === "osm"
          ? "© OpenStreetMap contributors"
          : "Satellite imagery via Cesium ion"}
      </a>
    </div>
  );
}
