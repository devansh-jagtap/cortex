"use client";

import { useState } from "react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { ChevronLeftIcon, ChevronRightIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DialogDescription, DialogOverlay, DialogPortal, DialogTitle } from "@/components/ui/dialog";
import { originalUrl, thumbnailUrl, type ImageItem } from "@/lib/backend";
import { formatBytes, formatCoordinates, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

interface PhotoViewerProps {
  items: (ImageItem & { score?: number })[];
  index: number | null;
  hasBridge: boolean;
  onIndexChange: (index: number | null) => void;
}

export function PhotoViewer({ items, index, hasBridge, onIndexChange }: PhotoViewerProps) {
  const item = index !== null ? items[index] : undefined;
  const [loadedId, setLoadedId] = useState<number | null>(null);
  const [failedId, setFailedId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<{ id: number; message: string } | null>(null);

  const go = (delta: number) => {
    if (index === null) return;
    const next = index + delta;
    if (next >= 0 && next < items.length) onIndexChange(next);
  };

  async function run(action: (id: number) => Promise<void>) {
    if (!item) return;
    setActionError(null);
    try {
      await action(item.id);
    } catch (err) {
      const message = err instanceof Error ? err.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, "") : "That didn't work.";
      setActionError({ id: item.id, message });
    }
  }

  const camera = item ? [item.camera_make, item.camera_model].filter(Boolean).join(" ") : "";

  return (
    <DialogPrimitive.Root open={item !== undefined} onOpenChange={(open) => !open && onIndexChange(null)}>
      <DialogPortal>
        <DialogOverlay className="bg-[#05060d]/80" />
        {item && (
          <DialogPrimitive.Content
            onKeyDown={(e) => {
              if (e.key === "ArrowRight") go(1);
              if (e.key === "ArrowLeft") go(-1);
            }}
            className="fixed inset-3 z-50 flex flex-col overflow-hidden rounded-xl bg-deep ring-1 ring-border outline-none duration-150 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-[0.98] data-closed:animate-out data-closed:fade-out-0 md:inset-8 md:flex-row"
          >
            <figure className="relative flex min-h-0 min-w-0 flex-1 items-center justify-center p-4 md:p-8">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={thumbnailUrl(item.id)}
                alt=""
                aria-hidden
                className={cn(
                  "absolute max-h-[calc(100%-4rem)] max-w-[calc(100%-4rem)] object-contain blur-md transition-opacity duration-300",
                  loadedId === item.id ? "opacity-0" : "opacity-70",
                )}
              />
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                key={item.id}
                src={originalUrl(item.id)}
                alt={item.filename}
                onLoad={() => setLoadedId(item.id)}
                onError={() => setFailedId(item.id)}
                className={cn(
                  "relative max-h-full max-w-full object-contain transition-opacity duration-300",
                  loadedId === item.id ? "opacity-100" : "opacity-0",
                )}
              />
              {failedId === item.id && (
                <p className="absolute bottom-6 rounded-md bg-card px-3 py-2 text-sm text-muted-foreground ring-1 ring-border">
                  The original file isn&apos;t available any more. It may have been moved or deleted.
                </p>
              )}
              <ViewerArrow side="left" disabled={index === 0} onClick={() => go(-1)} />
              <ViewerArrow side="right" disabled={index === items.length - 1} onClick={() => go(1)} />
            </figure>

            <aside className="flex max-h-[45%] w-full shrink-0 flex-col gap-6 overflow-y-auto border-t border-border bg-card p-6 md:max-h-none md:w-[320px] md:border-t-0 md:border-l">
              <div className="pr-8">
                <DialogTitle className="font-sans text-base font-semibold break-words">{item.filename}</DialogTitle>
                <DialogDescription className="mt-1.5 text-xs text-muted-foreground select-text">
                  <PathText path={item.path} />
                </DialogDescription>
              </div>

              <dl className="grid grid-cols-[auto_1fr] gap-x-5 gap-y-2.5 text-sm">
                <Fact label="Taken">{item.captured_at ? formatDate(item.captured_at) : "Not recorded"}</Fact>
                {item.width && item.height && (
                  <Fact label="Size">
                    {item.width.toLocaleString()} × {item.height.toLocaleString()}
                    <span className="text-muted-foreground">, {formatBytes(item.size)}</span>
                  </Fact>
                )}
                {camera && <Fact label="Camera">{camera}</Fact>}
                {item.latitude !== null && item.longitude !== null && (
                  <Fact label="Location">{formatCoordinates(item.latitude, item.longitude)}</Fact>
                )}
                {item.score !== undefined && (
                  <Fact label="Similarity">
                    <span className="tabular-nums">{item.score.toFixed(3)}</span>
                  </Fact>
                )}
              </dl>

              <div className="mt-auto flex flex-col gap-2">
                <Button
                  size="lg"
                  disabled={!hasBridge}
                  onClick={() => run((id) => window.cortex!.openImage(id))}
                >
                  Open original
                </Button>
                <Button
                  size="lg"
                  variant="outline"
                  disabled={!hasBridge}
                  onClick={() => run((id) => window.cortex!.showImageInFolder(id))}
                >
                  Show in folder
                </Button>
                {!hasBridge && (
                  <p className="text-xs text-muted-foreground">Opening files works in the Cortex desktop app.</p>
                )}
                {actionError?.id === item.id && <p className="text-xs text-destructive">{actionError.message}</p>}
                <p className="pt-2 text-xs text-muted-foreground tabular-nums">
                  {(index ?? 0) + 1} of {items.length.toLocaleString()}
                </p>
              </div>
            </aside>

            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon-sm" className="absolute top-3 right-3" aria-label="Close">
                <XIcon />
              </Button>
            </DialogPrimitive.Close>
          </DialogPrimitive.Content>
        )}
      </DialogPortal>
    </DialogPrimitive.Root>
  );
}

/** A file path that wraps at folder separators instead of mid-word. */
function PathText({ path }: { path: string }) {
  return (
    <>
      {path.split(/(?<=[\\/])/).map((part, i) => (
        <span key={i}>
          {part}
          <wbr />
        </span>
      ))}
    </>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}

function ViewerArrow({ side, disabled, onClick }: { side: "left" | "right"; disabled: boolean; onClick: () => void }) {
  if (disabled) return null;
  const Icon = side === "left" ? ChevronLeftIcon : ChevronRightIcon;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={side === "left" ? "Previous photo" : "Next photo"}
      className={cn(
        "absolute top-1/2 grid size-10 -translate-y-1/2 place-items-center rounded-full bg-black/40 text-white/80 backdrop-blur transition hover:bg-black/60 hover:text-white focus-visible:outline-2 focus-visible:outline-star",
        side === "left" ? "left-3" : "right-3",
      )}
    >
      <Icon className="size-5" />
    </button>
  );
}
