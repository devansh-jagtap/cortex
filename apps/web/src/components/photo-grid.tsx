"use client";

import { forwardRef, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";

import { thumbnailUrl, type ImageItem } from "@/lib/backend";
import { cn } from "@/lib/utils";

const ROW_HEIGHT = 190;
const GAP = 6;
// Screenshots can be extreme strips; clamp so every tile stays recognisable.
const MIN_ASPECT = 0.5;
const MAX_ASPECT = 3;
// Only the first screenful "develops"; animating blur on hundreds of tiles
// at once costs more than it shows.
const DEVELOP_LIMIT = 36;

interface Tile {
  item: ImageItem;
  index: number;
  aspect: number;
}

interface Row {
  tiles: Tile[];
  height: number;
  justified: boolean;
}

export interface PhotoGridHandle {
  focusFirst: () => void;
}

interface PhotoGridProps {
  items: ImageItem[];
  /** Changing this replays the reveal; the grid itself stays mounted. */
  revealKey: string;
  onOpen: (index: number) => void;
  onLeaveUp: () => void;
}

function trueAspect(item: ImageItem) {
  return item.width && item.height ? item.width / item.height : 1;
}

function aspectOf(item: ImageItem) {
  return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, trueAspect(item)));
}

/** Justified rows: fill each row edge to edge, keeping every photo's shape. */
function layoutRows(items: ImageItem[], width: number): Row[] {
  if (width <= 0) return [];
  const rows: Row[] = [];
  let tiles: Tile[] = [];
  let aspectSum = 0;
  items.forEach((item, index) => {
    const aspect = aspectOf(item);
    tiles.push({ item, index, aspect });
    aspectSum += aspect;
    const gaps = GAP * (tiles.length - 1);
    if (aspectSum * ROW_HEIGHT + gaps >= width) {
      rows.push({ tiles, height: (width - gaps) / aspectSum, justified: true });
      tiles = [];
      aspectSum = 0;
    }
  });
  if (tiles.length) rows.push({ tiles, height: ROW_HEIGHT, justified: false });
  return rows;
}

export const PhotoGrid = forwardRef<PhotoGridHandle, PhotoGridProps>(function PhotoGrid(
  { items, revealKey, onOpen, onLeaveUp },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tileRefs = useRef<Map<number, HTMLButtonElement>>(new Map());
  const [width, setWidth] = useState(0);
  const reduceMotion = useReducedMotion();

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const rows = useMemo(() => layoutRows(items, width), [items, width]);

  useImperativeHandle(ref, () => ({ focusFirst: () => tileRefs.current.get(0)?.focus() }), []);

  function neighbour(index: number, key: string): number | null {
    if (key === "ArrowRight") return index + 1 < items.length ? index + 1 : null;
    if (key === "ArrowLeft") return index > 0 ? index - 1 : null;
    const rowIndex = rows.findIndex((row) => row.tiles.some((t) => t.index === index));
    const targetRow = rows[rowIndex + (key === "ArrowDown" ? 1 : -1)];
    if (!targetRow) return null;
    const centre = (i: number) => {
      const rect = tileRefs.current.get(i)?.getBoundingClientRect();
      return rect ? rect.left + rect.width / 2 : 0;
    };
    const from = centre(index);
    let best = targetRow.tiles[0].index;
    for (const t of targetRow.tiles) {
      if (Math.abs(centre(t.index) - from) < Math.abs(centre(best) - from)) best = t.index;
    }
    return best;
  }

  return (
    <div ref={containerRef} className="flex flex-col" style={{ gap: GAP }}>
      {rows.map((row) => (
        <div key={`${revealKey}:${row.tiles[0].item.id}`} className="flex" style={{ gap: GAP, height: row.height }}>
          {row.tiles.map(({ item, index, aspect }) => {
            const develop = !reduceMotion && index < DEVELOP_LIMIT;
            // A strip too long for its tile is shown whole rather than cropped
            // and blown up into a blur.
            const letterbox = trueAspect(item) !== aspect;
            return (
              <motion.button
                key={`${revealKey}:${item.id}`}
                type="button"
                ref={(el) => {
                  if (el) tileRefs.current.set(index, el);
                  else tileRefs.current.delete(index);
                }}
                onClick={() => onOpen(index)}
                onKeyDown={(e) => {
                  if (!["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"].includes(e.key)) return;
                  e.preventDefault();
                  const next = neighbour(index, e.key);
                  if (next !== null) tileRefs.current.get(next)?.focus();
                  else if (e.key === "ArrowUp") onLeaveUp();
                }}
                initial={develop ? { opacity: 0, filter: "blur(12px) brightness(1.35)" } : false}
                animate={{ opacity: 1, filter: "blur(0px) brightness(1)" }}
                transition={{ duration: 0.5, delay: Math.min(index * 0.018, 0.4), ease: [0.2, 0.7, 0.2, 1] }}
                style={
                  row.justified
                    ? { flex: `${aspect} 1 0px`, minWidth: 0 }
                    : { width: aspect * row.height, flex: "none" }
                }
                aria-label={item.filename}
                className="group relative h-full overflow-hidden rounded-[3px] bg-card outline-none focus-visible:ring-2 focus-visible:ring-star focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                {/* Plain <img>: thumbnails come from the local backend; next/image's optimizer adds nothing here. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={thumbnailUrl(item.id)}
                  alt=""
                  loading="lazy"
                  decoding="async"
                  draggable={false}
                  className={cn("h-full w-full", letterbox ? "object-contain p-2" : "object-cover")}
                />
                <span className="pointer-events-none absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/75 to-transparent px-2 pt-6 pb-1.5 text-left text-[11px] text-white/90 opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100">
                  {item.filename}
                </span>
              </motion.button>
            );
          })}
        </div>
      ))}
    </div>
  );
});
