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

import { describeEntity } from "@/components/entity";
import { Button } from "@/components/ui/button";
import {
  getEntity,
  getGraph,
  thumbnailUrl,
  type EntityDetail,
  type GraphEdge,
  type GraphNode,
} from "@/lib/backend";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";

const INITIAL_NODES = 150;
const TYPE_COLOUR: Record<string, string> = {
  place: "var(--star)",
  event: "var(--foreground)",
  scene: "#a9adcf",
  person: "#a9adcf",
};

type Star = GraphNode & SimulationNodeDatum & { r: number; component: number };
type Link = SimulationLinkDatum<Star> & GraphEdge;

const radiusOf = (photos: number) => Math.min(34, 6 + Math.sqrt(photos) * 2.6);

/**
 * Lay out the graph once, synchronously: stars appear settled instead of
 * jiggling into place. Each connected group is pulled toward its own anchor
 * on a spiral (biggest groups nearest the centre), so unrelated groups read
 * as separate galaxies rather than one hairball.
 */
function layout(nodes: GraphNode[], edges: GraphEdge[], previous: Map<number, Star>): { stars: Star[]; links: Link[] } {
  const ids = new Set(nodes.map((n) => n.id));
  const parent = new Map<number, number>(nodes.map((n) => [n.id, n.id]));
  const find = (x: number): number => {
    while (parent.get(x)! !== x) {
      parent.set(x, parent.get(parent.get(x)!)!);
      x = parent.get(x)!;
    }
    return x;
  };
  const usable = edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  for (const e of usable) parent.set(find(e.source), find(e.target));

  const groups = new Map<number, number[]>();
  for (const n of nodes) groups.set(find(n.id), [...(groups.get(find(n.id)) ?? []), n.id]);
  const ordered = [...groups.values()].sort((a, b) => b.length - a.length);
  const anchor = new Map<number, { x: number; y: number }>();
  const componentOf = new Map<number, number>();
  ordered.forEach((members, i) => {
    const angle = i * 2.39996; // golden angle
    const distance = i === 0 ? 0 : 140 * Math.sqrt(i) + 40 * Math.sqrt(members.length);
    for (const id of members) {
      anchor.set(id, { x: Math.cos(angle) * distance, y: Math.sin(angle) * distance });
      componentOf.set(id, i);
    }
  });

  const stars: Star[] = nodes.map((n) => {
    const prev = previous.get(n.id);
    const a = anchor.get(n.id)!;
    return {
      ...n,
      r: radiusOf(n.photos),
      component: componentOf.get(n.id)!,
      x: prev?.x ?? a.x + (Math.random() - 0.5) * 60,
      y: prev?.y ?? a.y + (Math.random() - 0.5) * 60,
    };
  });
  const links: Link[] = usable.map((e) => ({ ...e }));

  forceSimulation(stars)
    .force(
      "link",
      forceLink<Star, Link>(links)
        .id((d) => d.id)
        .distance((l) => (l.kind === "appears_with" ? 110 : 70))
        .strength((l) => (l.kind === "appears_with" ? Math.min(0.6, 0.15 + l.weight * 0.05) : 0.7)),
    )
    .force("charge", forceManyBody<Star>().strength((d) => -60 - d.r * 8))
    .force("collide", forceCollide<Star>((d) => d.r + 6))
    .force("x", forceX<Star>((d) => anchor.get(d.id)!.x).strength(0.08))
    .force("y", forceY<Star>((d) => anchor.get(d.id)!.y).strength(0.08))
    .stop()
    .tick(previous.size ? 160 : 400);
  return { stars, links };
}

interface View {
  k: number;
  x: number;
  y: number;
}

export default function GalaxyView({ onShowPhotos }: { onShowPhotos: (entityId: number) => void }) {
  const [graph, setGraph] = useState<{ nodes: GraphNode[]; edges: GraphEdge[]; total: number } | null>(null);
  const [laidOut, setLaidOut] = useState<{ stars: Star[]; links: Link[] }>({ stars: [], links: [] });
  const [view, setView] = useState<View>({ k: 1, x: 0, y: 0 });
  const [hovered, setHovered] = useState<number | null>(null);
  const [selected, setSelected] = useState<EntityDetail | null>(null);
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
    const pad = 80;
    const w = Math.max(...xs) - Math.min(...xs) + pad * 2;
    const h = Math.max(...ys) - Math.min(...ys) + pad * 2;
    const k = Math.min(2, Math.max(0.2, Math.min(width / w, height / h)));
    const cx = (Math.max(...xs) + Math.min(...xs)) / 2;
    const cy = (Math.max(...ys) + Math.min(...ys)) / 2;
    setView({ k, x: width / 2 - cx * k, y: height / 2 - cy * k });
  }, []);

  const apply = useCallback(
    (nodes: GraphNode[], edges: GraphEdge[], total: number, refit: boolean) => {
      const result = layout(nodes, edges, positions.current);
      positions.current = new Map(result.stars.map((s) => [s.id, s]));
      setGraph({ nodes, edges, total });
      setLaidOut(result);
      if (refit) fit(result.stars);
    },
    [fit],
  );

  useEffect(() => {
    let cancelled = false;
    getGraph({ limit: INITIAL_NODES })
      .then((slice) => !cancelled && apply(slice.nodes, slice.edges, slice.total, true))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : "Couldn't load the galaxy."));
    return () => {
      cancelled = true;
    };
  }, [apply]);

  const neighbours = useMemo(() => {
    const focus = selected?.id ?? hovered;
    if (focus === null || focus === undefined) return null;
    const set = new Set<number>([focus]);
    for (const l of laidOut.links) {
      const s = (l.source as Star).id;
      const t = (l.target as Star).id;
      if (s === focus) set.add(t);
      if (t === focus) set.add(s);
    }
    return set;
  }, [selected, hovered, laidOut.links]);

  async function select(id: number) {
    try {
      setSelected(await getEntity(id));
    } catch {
      setSelected(null);
    }
  }

  async function expand(id: number) {
    if (!graph) return;
    const slice = await getGraph({ focus: id, limit: 40 });
    const known = new Map(graph.nodes.map((n) => [n.id, n]));
    for (const n of slice.nodes) known.set(n.id, n);
    const edgeKey = (e: GraphEdge) => `${e.source}-${e.target}-${e.kind}`;
    const edges = new Map(graph.edges.map((e) => [edgeKey(e), e]));
    for (const e of slice.edges) edges.set(edgeKey(e), e);
    // New stars start beside the one being expanded.
    const origin = positions.current.get(id);
    for (const n of slice.nodes) {
      if (!positions.current.has(n.id) && origin) {
        positions.current.set(n.id, { ...(n as Star), x: origin.x! + (Math.random() - 0.5) * 80, y: origin.y! + (Math.random() - 0.5) * 80 });
      }
    }
    apply([...known.values()], [...edges.values()], graph.total, false);
  }

  function zoomBy(factor: number, cx?: number, cy?: number) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const px = cx ?? rect.width / 2;
    const py = cy ?? rect.height / 2;
    setView((v) => {
      const k = Math.min(4, Math.max(0.15, v.k * factor));
      return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
    });
  }

  const empty = graph !== null && graph.nodes.length === 0;
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of graph?.nodes ?? []) c[n.type] = (c[n.type] ?? 0) + 1;
    return c;
  }, [graph]);

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden">
      <svg
        ref={svgRef}
        className="absolute inset-0 h-full w-full cursor-grab touch-none select-none active:cursor-grabbing"
        role="application"
        aria-label="Galaxy of places, events, and scenes in your photos"
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
          {laidOut.stars
            .filter((s) => s.cover && s.r >= 14)
            .map((s) => (
              <clipPath key={s.id} id={`star-clip-${s.id}`}>
                <circle r={s.r - 2} />
              </clipPath>
            ))}
          <filter id="star-glow" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          {laidOut.links.map((l) => {
            const s = l.source as Star;
            const t = l.target as Star;
            const lit = neighbours?.has(s.id) && neighbours?.has(t.id) && (s.id === (selected?.id ?? hovered) || t.id === (selected?.id ?? hovered));
            return (
              <line
                key={`${s.id}-${t.id}-${l.kind}`}
                x1={s.x}
                y1={s.y}
                x2={t.x}
                y2={t.y}
                stroke={lit ? "var(--star)" : "var(--foreground)"}
                strokeOpacity={lit ? 0.6 : selected ? 0.04 : 0.1 + Math.min(0.15, Math.log1p(l.weight) * 0.03)}
                strokeWidth={(lit ? 1.4 : 1) / Math.sqrt(view.k)}
                strokeDasharray={l.kind === "part_of" ? "3 3" : undefined}
              />
            );
          })}

          {laidOut.stars.map((s) => {
            // Only a deliberate click dims the rest; hovering just lights up connections.
            const dim = selected !== null && neighbours !== null && !neighbours.has(s.id);
            const active = s.id === selected?.id;
            // Small galaxies name every star; big ones name the large stars and whatever is in focus.
            const showLabel = laidOut.stars.length <= 60 || s.r >= 12 || neighbours?.has(s.id) || view.k > 1.6;
            const colour = TYPE_COLOUR[s.type];
            return (
              <g
                key={s.id}
                transform={`translate(${s.x},${s.y})`}
                role="button"
                tabIndex={0}
                aria-label={`${s.name}, ${describeEntity(s)}, ${plural(s.photos, "photo")}`}
                className="cursor-pointer outline-none [&:focus-visible>circle.ring]:stroke-star [&:focus-visible>circle.ring]:stroke-[3]"
                style={{ opacity: dim ? 0.18 : 1, transition: "opacity 150ms" }}
                onPointerEnter={() => setHovered(s.id)}
                onPointerLeave={() => setHovered((h) => (h === s.id ? null : h))}
                onClick={() => void select(s.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    void select(s.id);
                  }
                }}
              >
                {s.cover && s.r >= 14 ? (
                  <>
                    <circle r={s.r + (active ? 5 : 0)} fill={colour} opacity={active ? 0.35 : 0} filter="url(#star-glow)" />
                    <image
                      href={thumbnailUrl(s.cover)}
                      x={-s.r}
                      y={-s.r}
                      width={s.r * 2}
                      height={s.r * 2}
                      preserveAspectRatio="xMidYMid slice"
                      clipPath={`url(#star-clip-${s.id})`}
                    />
                    <circle className="ring" r={s.r - 1} fill="none" stroke={colour} strokeWidth={active ? 2.5 : 1.5} />
                  </>
                ) : (
                  <circle className="ring" r={s.r * 0.6} fill={colour} filter="url(#star-glow)" stroke="none" />
                )}
                {showLabel && (
                  <text
                    y={s.r + 13 / Math.max(1, view.k)}
                    textAnchor="middle"
                    className="fill-foreground"
                    style={{ fontSize: 11 / Math.max(0.6, view.k), paintOrder: "stroke", stroke: "var(--background)", strokeWidth: 3 / Math.max(0.6, view.k) }}
                  >
                    {s.name}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      {graph && !empty && (
        <div className="pointer-events-none absolute top-4 left-4 flex flex-col gap-1.5 rounded-lg bg-card/85 px-3 py-2.5 text-xs ring-1 ring-border backdrop-blur">
          {[
            ["place", "Places"],
            ["event", "Events"],
            ["scene", "Scenes"],
          ].map(([type, label]) =>
            counts[type] ? (
              <span key={type} className="flex items-center gap-2 text-muted-foreground">
                <span className="size-2 rounded-full" style={{ background: TYPE_COLOUR[type] }} />
                <span className="text-foreground/90">{counts[type]}</span> {label.toLowerCase()}
              </span>
            ) : null,
          )}
          {graph.total > graph.nodes.length && (
            <span className="pt-1 text-muted-foreground">
              Showing the {graph.nodes.length} biggest of {graph.total}. Click a star, then Expand.
            </span>
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
              {error ? "The galaxy couldn't load." : "Your galaxy is still forming."}
            </p>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {error ??
                "Cortex draws it from where your photos were taken, what's in them, and the events they belong to. It fills in as photos are analyzed."}
            </p>
          </div>
        </div>
      )}

      {selected && (
        <aside className="absolute top-4 right-4 bottom-4 flex w-[340px] max-w-[calc(100%-2rem)] animate-in flex-col overflow-hidden rounded-xl bg-card/95 ring-1 ring-border backdrop-blur fade-in-0 slide-in-from-right-4 duration-200">
          <div className="flex items-start justify-between gap-3 border-b border-border p-4">
            <div className="min-w-0">
              <p className="font-serif text-2xl leading-tight break-words">{selected.name}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {describeEntity(selected)}
                <span className="ml-2 text-foreground/80">{plural(selected.photos_total, "photo")}</span>
              </p>
            </div>
            <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={() => setSelected(null)}>
              <XIcon />
            </Button>
          </div>
          <ul className="grid grid-cols-3 gap-1.5 overflow-y-auto p-3">
            {selected.photos.slice(0, 24).map((photo) => (
              <li key={photo.id} className="aspect-square overflow-hidden rounded-[3px] bg-deep">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={thumbnailUrl(photo.id)} alt={photo.filename} loading="lazy" className="h-full w-full object-cover" />
              </li>
            ))}
          </ul>
          <div className="mt-auto flex gap-2 border-t border-border p-3">
            <Button size="sm" className={cn("flex-1")} onClick={() => onShowPhotos(selected.id)}>
              Show all photos
            </Button>
            <Button size="sm" variant="outline" onClick={() => void expand(selected.id)}>
              Expand
            </Button>
          </div>
        </aside>
      )}
    </div>
  );
}
