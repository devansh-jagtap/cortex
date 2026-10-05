"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { MinusIcon, PlusIcon, ScanIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  getImagesByIds,
  getPhotoGraph,
  thumbnailUrl,
  type ImageItem,
  type PhotoGraph,
  type PhotoLink,
  type PhotoNode,
} from "@/lib/backend";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";

const INITIAL_PHOTOS = 400;

/** Few photos are drawn big enough to recognise; many get smaller to fit. */
const radiusFor = (count: number) => Math.min(34, Math.max(14, 40 - Math.sqrt(count) * 1.2));

type Star = PhotoNode & SimulationNodeDatum;
type Link = SimulationLinkDatum<Star> & PhotoLink;

/**
 * Lay the network out once, synchronously, so photos appear settled. Each
 * group of look-alikes is pulled toward its own anchor on a spiral (biggest
 * groups nearest the centre): groups with nothing in common float apart as
 * separate galaxies; photos unlike anything else drift on their own.
 */
function layout(graph: PhotoGraph, previous: Map<number, Star>): { stars: Star[]; links: Link[] } {
  const r = radiusFor(graph.nodes.length);
  const sizes = new Map<number, number>();
  for (const n of graph.nodes) sizes.set(n.cluster, (sizes.get(n.cluster) ?? 0) + 1);
  const order = [...sizes.entries()].sort((a, b) => b[1] - a[1]).map(([cluster]) => cluster);
  const anchors = new Map<number, { x: number; y: number }>();
  order.forEach((cluster, i) => {
    const angle = i * 2.39996; // golden angle
    const distance = i === 0 ? 0 : r * (4.5 * Math.sqrt(i) + 1.2 * Math.sqrt(sizes.get(cluster)!));
    anchors.set(cluster, { x: Math.cos(angle) * distance, y: Math.sin(angle) * distance });
  });

  const stars: Star[] = graph.nodes.map((n) => {
    const prev = previous.get(n.id);
    const a = anchors.get(n.cluster)!;
    return { ...n, x: prev?.x ?? a.x + (Math.random() - 0.5) * 40, y: prev?.y ?? a.y + (Math.random() - 0.5) * 40 };
  });
  const ids = new Set(stars.map((s) => s.id));
  const links: Link[] = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ ...e }));

  forceSimulation(stars)
    .force(
      "link",
      forceLink<Star, Link>(links)
        .id((d) => d.id)
        .distance((l) => r * (2.4 + (1 - l.similarity) * 5))
        .strength((l) => 0.3 + l.similarity * 0.6),
    )
    .force("charge", forceManyBody<Star>().strength(-3.5 * r))
    .force("collide", forceCollide<Star>(r * 1.25))
    .force("x", forceX<Star>((d) => anchors.get(d.cluster)!.x).strength(0.07))
    .force("y", forceY<Star>((d) => anchors.get(d.cluster)!.y).strength(0.07))
    .stop()
    .tick(previous.size ? 180 : 400);
  return { stars, links };
}

interface View {
  k: number;
  x: number;
  y: number;
}

export default function GalaxyView({ onOpen }: { onOpen: (items: ImageItem[], index: number) => void }) {
  const [graph, setGraph] = useState<PhotoGraph | null>(null);
  const [laidOut, setLaidOut] = useState<{ stars: Star[]; links: Link[] }>({ stars: [], links: [] });
  const [view, setView] = useState<View>({ k: 1, x: 0, y: 0 });
  const [hovered, setHovered] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const positions = useRef(new Map<number, Star>());
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  const fit = useCallback((stars: Star[]) => {
    const svg = svgRef.current;
    if (!svg || stars.length === 0) return;
    const { width, height } = svg.getBoundingClientRect();
    const xs = stars.map((s) => s.x!);
    const ys = stars.map((s) => s.y!);
    const pad = 70;
    const w = Math.max(...xs) - Math.min(...xs) + pad * 2;
    const h = Math.max(...ys) - Math.min(...ys) + pad * 2;
    const k = Math.min(1.8, Math.max(0.15, Math.min(width / w, height / h)));
    const cx = (Math.max(...xs) + Math.min(...xs)) / 2;
    const cy = (Math.max(...ys) + Math.min(...ys)) / 2;
    setView({ k, x: width / 2 - cx * k, y: height / 2 - cy * k });
  }, []);

  const apply = useCallback(
    (next: PhotoGraph, refit: boolean) => {
      const result = layout(next, positions.current);
      positions.current = new Map(result.stars.map((s) => [s.id, s]));
      setGraph(next);
      setLaidOut(result);
      if (refit) fit(result.stars);
    },
    [fit],
  );

  useEffect(() => {
    let cancelled = false;
    getPhotoGraph({ limit: INITIAL_PHOTOS })
      .then((g) => !cancelled && apply(g, true))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : "Couldn't load the galaxy."));
    return () => {
      cancelled = true;
    };
  }, [apply]);

  /** Each photo's look-alikes in the current view, most similar first. */
  const neighbours = useMemo(() => {
    const map = new Map<number, { id: number; similarity: number }[]>();
    for (const l of laidOut.links) {
      const s = (l.source as Star).id;
      const t = (l.target as Star).id;
      map.set(s, [...(map.get(s) ?? []), { id: t, similarity: l.similarity }]);
      map.set(t, [...(map.get(t) ?? []), { id: s, similarity: l.similarity }]);
    }
    for (const list of map.values()) list.sort((a, b) => b.similarity - a.similarity);
    return map;
  }, [laidOut.links]);

  const focusId = selected ?? hovered;
  const lit = useMemo(() => {
    if (focusId === null) return null;
    return new Set([focusId, ...(neighbours.get(focusId) ?? []).map((n) => n.id)]);
  }, [focusId, neighbours]);

  const labels = useMemo(() => {
    if (!graph) return [];
    return graph.clusters
      .filter((c) => c.label && c.size >= 2)
      .map((c) => {
        const members = laidOut.stars.filter((s) => s.cluster === c.id);
        if (members.length === 0) return null;
        const x = members.reduce((sum, s) => sum + s.x!, 0) / members.length;
        const y = Math.min(...members.map((s) => s.y!)) - radiusFor(laidOut.stars.length) - 18;
        return { id: c.id, label: c.label!, x, y };
      })
      .filter((l): l is { id: number; label: string; x: number; y: number } => l !== null);
  }, [graph, laidOut.stars]);

  /** Select a photo, sliding the view so it isn't hidden under the side panel. */
  function select(star: Star) {
    setSelected(star.id);
    const svg = svgRef.current;
    if (!svg) return;
    const width = svg.getBoundingClientRect().width;
    const screenX = star.x! * view.k + view.x;
    const visibleRight = width - 380;
    if (screenX > visibleRight - 40) setView((v) => ({ ...v, x: v.x - (screenX - (visibleRight - 80)) }));
  }

  async function openPhotos(ids: number[], index: number) {
    const page = await getImagesByIds(ids);
    const byId = new Map(page.items.map((item) => [item.id, item]));
    const items = ids.map((id) => byId.get(id)).filter((item): item is ImageItem => !!item);
    if (items.length) onOpen(items, Math.min(index, items.length - 1));
  }

  async function showMoreLikeThis(id: number) {
    if (!graph) return;
    const around = await getPhotoGraph({ focus: id });
    const known = new Map(graph.nodes.map((n) => [n.id, n]));
    // New photos join the group of the photo they were found around.
    const clusterOf = known.get(id)?.cluster ?? 0;
    for (const n of around.nodes) if (!known.has(n.id)) known.set(n.id, { ...n, cluster: clusterOf });
    const key = (e: PhotoLink) => `${Math.min(e.source, e.target)}-${Math.max(e.source, e.target)}`;
    const edges = new Map(graph.edges.map((e) => [key(e), e]));
    for (const e of around.edges) edges.set(key(e), e);
    const origin = positions.current.get(id);
    for (const n of around.nodes) {
      if (!positions.current.has(n.id) && origin) {
        positions.current.set(n.id, { ...n, x: origin.x! + (Math.random() - 0.5) * 60, y: origin.y! + (Math.random() - 0.5) * 60 });
      }
    }
    apply({ ...graph, nodes: [...known.values()], edges: [...edges.values()] }, false);
  }

  function zoomBy(factor: number, cx?: number, cy?: number) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const px = cx ?? rect.width / 2;
    const py = cy ?? rect.height / 2;
    setView((v) => {
      const k = Math.min(5, Math.max(0.1, v.k * factor));
      return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
    });
  }

  const empty = graph !== null && graph.nodes.length === 0;
  const selectedStar = selected !== null ? laidOut.stars.find((s) => s.id === selected) : undefined;
  const lookAlikes = selected !== null ? neighbours.get(selected) ?? [] : [];
  const groups = graph ? new Set(graph.nodes.map((n) => n.cluster)).size : 0;
  const radius = radiusFor(laidOut.stars.length);

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden">
      <svg
        ref={svgRef}
        className="absolute inset-0 h-full w-full cursor-grab touch-none select-none active:cursor-grabbing"
        role="application"
        aria-label="Galaxy: your photos, joined when they look alike"
        onWheel={(e) => {
          const rect = svgRef.current!.getBoundingClientRect();
          zoomBy(Math.exp(-e.deltaY * 0.0015), e.clientX - rect.left, e.clientY - rect.top);
        }}
        onPointerDown={(e) => {
          if (e.target !== e.currentTarget) return;
          drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (d) setView((v) => ({ ...v, x: d.vx + e.clientX - d.x, y: d.vy + e.clientY - d.y }));
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (d && Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) < 4) setSelected(null);
        }}
        onKeyDown={(e) => e.key === "Escape" && setSelected(null)}
      >
        <defs>
          {/* One round clip shared by every photo (in the photo's own box). */}
          <clipPath id="photo-round" clipPathUnits="objectBoundingBox">
            <circle cx="0.5" cy="0.5" r="0.5" />
          </clipPath>
        </defs>

        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          {laidOut.links.map((l) => {
            const s = l.source as Star;
            const t = l.target as Star;
            const on = focusId !== null && (s.id === focusId || t.id === focusId);
            return (
              <line
                key={`${s.id}-${t.id}`}
                x1={s.x}
                y1={s.y}
                x2={t.x}
                y2={t.y}
                stroke={on ? "var(--star)" : "var(--foreground)"}
                strokeOpacity={on ? 0.7 : selected !== null ? 0.05 : 0.08 + (l.similarity - 0.55) * 0.6}
                strokeWidth={(on ? 1.6 : 1) / Math.sqrt(view.k)}
              />
            );
          })}

          {labels.map((l) => (
            <text
              key={`label-${l.id}`}
              x={l.x}
              y={l.y}
              textAnchor="middle"
              className="fill-foreground font-serif"
              style={{ fontSize: 15 / Math.max(0.5, view.k), opacity: selected !== null ? 0.3 : 0.75 }}
            >
              {l.label}
            </text>
          ))}

          {laidOut.stars.map((s) => {
            const active = s.id === selected;
            const dim = selected !== null && lit !== null && !lit.has(s.id);
            const r = active ? radius * 1.4 : s.id === hovered ? radius * 1.15 : radius;
            return (
              <g
                key={s.id}
                transform={`translate(${s.x},${s.y})`}
                role="button"
                tabIndex={0}
                aria-label={s.filename}
                className="cursor-pointer outline-none [&:focus-visible>circle]:stroke-star"
                style={{ opacity: dim ? 0.22 : 1, transition: "opacity 150ms" }}
                onPointerEnter={() => setHovered(s.id)}
                onPointerLeave={() => setHovered((h) => (h === s.id ? null : h))}
                onClick={() => select(s)}
                onDoubleClick={() => void openPhotos([s.id, ...(neighbours.get(s.id) ?? []).map((n) => n.id)], 0)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void openPhotos([s.id, ...(neighbours.get(s.id) ?? []).map((n) => n.id)], 0);
                  if (e.key === " ") {
                    e.preventDefault();
                    select(s);
                  }
                }}
              >
                <title>{s.filename}</title>
                <image
                  href={thumbnailUrl(s.id)}
                  x={-r}
                  y={-r}
                  width={r * 2}
                  height={r * 2}
                  preserveAspectRatio="xMidYMid slice"
                  clipPath="url(#photo-round)"
                />
                <circle
                  r={r}
                  fill="none"
                  stroke={active || (lit?.has(s.id) && focusId !== null) ? "var(--star)" : "var(--border)"}
                  strokeWidth={active ? 2.5 : 1.5}
                />
              </g>
            );
          })}
        </g>
      </svg>

      {graph && !empty && (
        <div className="pointer-events-none absolute top-4 left-4 max-w-[260px] rounded-lg bg-card/85 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground ring-1 ring-border backdrop-blur">
          <p>
            <span className="text-foreground/90">{plural(graph.nodes.length, "photo")}</span> in{" "}
            <span className="text-foreground/90">{plural(groups, "group")}</span>. Lines join photos that look alike.
          </p>
          {graph.total > graph.nodes.length && (
            <p className="mt-1">
              Showing the newest {graph.nodes.length.toLocaleString()} of {graph.total.toLocaleString()}. Click a
              photo, then Show more like this.
            </p>
          )}
        </div>
      )}

      <div className="absolute bottom-4 left-4 flex flex-col overflow-hidden rounded-lg bg-card/90 ring-1 ring-border backdrop-blur">
        <Button variant="ghost" size="icon-sm" aria-label="Zoom in" onClick={() => zoomBy(1.4)}>
          <PlusIcon />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="Zoom out" onClick={() => zoomBy(1 / 1.4)}>
          <MinusIcon />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="Fit everything" onClick={() => fit(laidOut.stars)}>
          <ScanIcon />
        </Button>
      </div>

      {(empty || error) && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center p-6">
          <div className="max-w-sm rounded-xl bg-card/95 p-6 ring-1 ring-border">
            <p className="font-serif text-2xl leading-tight">
              {error ? "The galaxy couldn't load." : "Your galaxy forms once photos are analyzed."}
            </p>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {error ?? "Each photo becomes a star, joined to the photos that look like it. Add a folder to begin."}
            </p>
          </div>
        </div>
      )}

      {selectedStar && (
        <aside className="absolute top-4 right-4 bottom-4 flex w-[340px] max-w-[calc(100%-2rem)] animate-in flex-col overflow-hidden rounded-xl bg-card/95 ring-1 ring-border backdrop-blur fade-in-0 slide-in-from-right-4 duration-200">
          <div className="relative aspect-[4/3] shrink-0 bg-deep">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={thumbnailUrl(selectedStar.id)} alt={selectedStar.filename} className="h-full w-full object-contain" />
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Close"
              className="absolute top-2 right-2 bg-black/40 text-white hover:bg-black/60"
              onClick={() => setSelected(null)}
            >
              <XIcon />
            </Button>
          </div>
          <div className="border-b border-border px-4 py-3">
            <p className="truncate text-sm font-medium" title={selectedStar.filename}>
              {selectedStar.filename}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {lookAlikes.length
                ? `Looks like ${plural(lookAlikes.length, "photo")} here`
                : "Doesn't look like anything else here yet"}
            </p>
          </div>
          <ul className="grid grid-cols-3 gap-1.5 overflow-y-auto p-3">
            {lookAlikes.map((n, i) => (
              <li key={n.id}>
                <button
                  type="button"
                  onClick={() => void openPhotos([selectedStar.id, ...lookAlikes.map((x) => x.id)], i + 1)}
                  className="relative block aspect-square w-full overflow-hidden rounded-[3px] bg-deep outline-none focus-visible:ring-2 focus-visible:ring-star"
                  title={`${Math.round(n.similarity * 100)}% alike`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={thumbnailUrl(n.id)} alt="" loading="lazy" className="h-full w-full object-cover" />
                  <span className="absolute right-1 bottom-1 rounded bg-black/60 px-1 text-[10px] text-white/90 tabular-nums">
                    {Math.round(n.similarity * 100)}%
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className={cn("mt-auto flex gap-2 border-t border-border p-3")}>
            <Button
              size="sm"
              className="flex-1"
              onClick={() => void openPhotos([selectedStar.id, ...lookAlikes.map((x) => x.id)], 0)}
            >
              Open
            </Button>
            <Button size="sm" variant="outline" onClick={() => void showMoreLikeThis(selectedStar.id)}>
              Show more like this
            </Button>
          </div>
        </aside>
      )}
    </div>
  );
}
