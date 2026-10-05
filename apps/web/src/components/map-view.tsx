"use client";

import "maplibre-gl/dist/maplibre-gl.css";

import { useEffect, useRef, useState } from "react";
import { GeoJSONSource, LngLatBounds, Map as MapLibre, Marker, NavigationControl, setWorkerUrl } from "maplibre-gl";
import { XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { getImagesByIds, getMapPoints, thumbnailUrl, type ImageItem } from "@/lib/backend";
import { formatCoordinates, plural } from "@/lib/format";

// The one part of Cortex that uses the internet: map tiles for the areas
// you look at. No photo data is sent. MapLibre can later switch to offline
// tiles without changing anything else.
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const MAX_LEAVES = 500;

// Served from public/ by scripts/copy-maplibre-worker.mjs (bundlers can't follow MapLibre's worker URL).
setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

interface Place {
  items: ImageItem[];
  count: number;
  lng: number;
  lat: number;
}

export default function MapView({ onOpen }: { onOpen: (items: ImageItem[], index: number) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [pointCount, setPointCount] = useState<number | null>(null);
  const [place, setPlace] = useState<Place | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const map = new MapLibre({
      container: containerRef.current!,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: [TILE_URL],
            tileSize: 256,
            maxzoom: 19,
            attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
          },
        },
        // OSM tiles are light. Inverting their lightness (min above max) turns
        // land dark and labels light; laid over the night colour they tint indigo.
        layers: [
          { id: "night", type: "background", paint: { "background-color": "#0e1020" } },
          {
            id: "osm",
            type: "raster",
            source: "osm",
            paint: {
              "raster-saturation": -1,
              "raster-brightness-min": 0.9,
              "raster-brightness-max": 0.1,
              "raster-opacity": 0.5,
            },
          },
        ],
      },
      center: [78, 21],
      zoom: 1.5,
      attributionControl: { compact: true },
    });
    map.addControl(new NavigationControl({ showCompass: false }), "bottom-left");

    const markers = new globalThis.Map<number, Marker>();
    let onScreen = new globalThis.Map<number, Marker>();

    async function showPlace(ids: number[], count: number, lng: number, lat: number) {
      const page = await getImagesByIds(ids);
      if (!cancelled) setPlace({ items: page.items, count, lng, lat });
    }

    function updateClusterMarkers() {
      const next = new globalThis.Map<number, Marker>();
      for (const feature of map.querySourceFeatures("photos")) {
        const props = feature.properties;
        if (!props?.cluster || feature.geometry.type !== "Point") continue;
        const clusterId = props.cluster_id as number;
        const count = props.point_count as number;
        const [lng, lat] = feature.geometry.coordinates as [number, number];
        let marker = markers.get(clusterId);
        if (!marker) {
          const size = Math.round(30 + Math.min(26, Math.log2(count) * 4));
          const el = document.createElement("button");
          el.type = "button";
          el.textContent = count >= 1000 ? `${Math.round(count / 100) / 10}k` : String(count);
          el.setAttribute("aria-label", `${count} photos here`);
          el.style.width = el.style.height = `${size}px`;
          el.className =
            "grid place-items-center rounded-full bg-star/20 text-[11px] font-semibold text-foreground ring-2 ring-star/80 backdrop-blur-[2px] transition-colors hover:bg-star/40 focus-visible:outline-2 focus-visible:outline-star";
          el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const source = map.getSource<GeoJSONSource>("photos");
            const leaves = await source!.getClusterLeaves(clusterId, MAX_LEAVES, 0);
            void showPlace(leaves.map((f) => f.properties!.id as number), count, lng, lat);
          });
          marker = new Marker({ element: el }).setLngLat([lng, lat]);
          markers.set(clusterId, marker);
        }
        next.set(clusterId, marker);
        if (!onScreen.has(clusterId)) marker.addTo(map);
      }
      for (const [id, marker] of onScreen) if (!next.has(id)) marker.remove();
      onScreen = next;
    }

    map.on("load", async () => {
      let data;
      try {
        data = await getMapPoints();
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "Couldn't load photo locations.");
        return;
      }
      if (cancelled) return;
      setPointCount(data.features.length);
      map.addSource("photos", { type: "geojson", data, cluster: true, clusterRadius: 48, clusterMaxZoom: 16 });
      map.addLayer({
        id: "photo-points",
        type: "circle",
        source: "photos",
        filter: ["!", ["has", "point_count"]],
        paint: {
          "circle-radius": 6,
          "circle-color": "#e9e6f2",
          "circle-stroke-color": "#f3c969",
          "circle-stroke-width": 2,
        },
      });
      map.on("click", "photo-points", (e) => {
        const feature = e.features?.[0];
        if (!feature || feature.geometry.type !== "Point") return;
        const [lng, lat] = feature.geometry.coordinates as [number, number];
        void showPlace([feature.properties.id as number], 1, lng, lat);
      });
      map.on("mouseenter", "photo-points", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "photo-points", () => (map.getCanvas().style.cursor = ""));
      map.on("render", () => {
        if (map.getSource("photos") && map.isSourceLoaded("photos")) updateClusterMarkers();
      });

      if (data.features.length) {
        const bounds = new LngLatBounds();
        for (const f of data.features) bounds.extend(f.geometry.coordinates);
        map.fitBounds(bounds, { padding: 96, maxZoom: 11, duration: 0 });
      }
    });

    return () => {
      cancelled = true;
      map.remove();
    };
  }, []);

  return (
    <div className="relative min-h-0 flex-1">
      {/* Inline style: MapLibre's stylesheet sets `.maplibregl-map { position: relative }`, which would override a class. */}
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />

      {(pointCount === 0 || loadError) && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center p-6">
          <div className="max-w-sm rounded-xl bg-card/95 p-6 ring-1 ring-border backdrop-blur">
            <p className="font-serif text-2xl leading-tight">
              {loadError ? "The map couldn't load your photos." : "None of your photos have a location yet."}
            </p>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {loadError ??
                "Photos taken on a phone usually record where they were taken. Screenshots and downloads don't, so they won't appear here."}
            </p>
          </div>
        </div>
      )}

      {place && (
        <aside className="absolute top-4 right-4 bottom-4 flex w-[340px] max-w-[calc(100%-2rem)] flex-col overflow-hidden rounded-xl bg-card/95 ring-1 ring-border backdrop-blur animate-in fade-in-0 slide-in-from-right-4 duration-200">
          <div className="flex items-start justify-between gap-3 border-b border-border p-4">
            <div>
              <p className="font-serif text-2xl leading-tight">{plural(place.count, "photo")}</p>
              <p className="mt-1 text-xs text-muted-foreground">near {formatCoordinates(place.lat, place.lng)}</p>
            </div>
            <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={() => setPlace(null)}>
              <XIcon />
            </Button>
          </div>
          <ul className="grid grid-cols-3 gap-1.5 overflow-y-auto p-3">
            {place.items.map((item, index) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => onOpen(place.items, index)}
                  aria-label={item.filename}
                  className="block aspect-square w-full overflow-hidden rounded-[3px] bg-deep outline-none focus-visible:ring-2 focus-visible:ring-star"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={thumbnailUrl(item.id)} alt="" loading="lazy" className="h-full w-full object-cover" />
                </button>
              </li>
            ))}
          </ul>
          {place.count > place.items.length && (
            <p className="border-t border-border px-4 py-2 text-xs text-muted-foreground">
              Showing the first {place.items.length.toLocaleString()}. Zoom in to see the rest.
            </p>
          )}
        </aside>
      )}
    </div>
  );
}
