"use client";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { IndexStatus, Library } from "@/lib/backend";
import { baseName, formatRelative, plural } from "@/lib/format";
import { cn } from "@/lib/utils";

type Tone = "busy" | "ok" | "warn" | "error";

interface Summary {
  tone: Tone;
  text: string;
  detail?: string;
}

/** One sentence for the header: what Cortex is doing right now. */
export function summarise(status: IndexStatus, online: boolean | null): Summary {
  if (online === false) return { tone: "error", text: "Engine offline" };
  if (online === null) return { tone: "ok", text: "Connecting" };

  const stats = status.stats;
  const embedding = status.embedding;
  if (status.model?.state === "downloading") return { tone: "busy", text: "Downloading the AI model" };
  if (status.model?.state === "loading") return { tone: "busy", text: "Loading the AI model" };
  if (status.state === "running" && stats) {
    return stats.supported_images
      ? { tone: "busy", text: `Indexing ${stats.processed.toLocaleString()} of ${stats.supported_images.toLocaleString()}` }
      : { tone: "busy", text: "Looking for photos" };
  }
  if (embedding?.state === "running") {
    return {
      tone: "busy",
      text: `Analyzing photos ${(embedding.processed ?? 0).toLocaleString()} of ${(embedding.total ?? 0).toLocaleString()}`,
    };
  }
  if (status.updating) return { tone: "busy", text: "Updating changes" };
  if (status.organizing?.state === "running") return { tone: "busy", text: "Organizing by place and scene" };
  if (["interrupted", "cancelled", "failed"].includes(status.state)) return { tone: "warn", text: "Indexing stopped" };
  if (embedding?.state === "error" || status.model?.state === "error") return { tone: "warn", text: "AI model unavailable" };
  const watching = status.watching ?? 0;
  if (watching > 0) {
    return {
      tone: "ok",
      text: `Watching ${plural(watching, "folder")}`,
      detail: status.last_activity_at ? `updated ${formatRelative(status.last_activity_at)}` : undefined,
    };
  }
  return { tone: "ok", text: "Ready" };
}

/** 0..1 while something with a known size is running, otherwise null. */
export function progressOf(status: IndexStatus): number | null {
  const stats = status.stats;
  if (status.state === "running" && stats?.supported_images) return stats.processed / stats.supported_images;
  const e = status.embedding;
  if (e?.state === "running" && e.total) return (e.processed ?? 0) / e.total;
  return null;
}

interface StatusMenuProps {
  status: IndexStatus;
  online: boolean | null;
  library: Library | null;
  hasBridge: boolean;
  onAddFolder: () => void;
  onIndexComputer: () => void;
  onScan: (path: string) => void;
  onRemove: (root: { id: number; path: string }) => void;
  onCancel: () => void;
}

export function StatusMenu({
  status,
  online,
  library,
  hasBridge,
  onAddFolder,
  onIndexComputer,
  onScan,
  onRemove,
  onCancel,
}: StatusMenuProps) {
  const summary = summarise(status, online);
  const stats = status.stats;
  const scanning = status.state === "running";
  const embedding = status.embedding?.state === "running";
  const stopped = ["interrupted", "cancelled", "failed"].includes(status.state) && stats?.root_path;
  const aiError = status.embedding?.state === "error" ? status.embedding.error : status.model?.error;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex items-center gap-2 rounded-full px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-star aria-expanded:bg-accent aria-expanded:text-foreground"
        >
          <Dot tone={summary.tone} />
          <span className="text-foreground/90 tabular-nums">{summary.text}</span>
          {summary.detail && <span className="hidden md:inline">{summary.detail}</span>}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={8} className="w-[380px] gap-0 p-0">
        <div className="border-b border-border p-4">
          <p className="font-serif text-lg leading-tight">Library</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {library
              ? `${plural(library.images_indexed, "photo")} from ${plural(library.roots.length, "folder")}. Cortex watches these folders and picks up new, changed, and deleted photos on its own.`
              : "Connecting to the Cortex engine…"}
          </p>
        </div>

        {(scanning || embedding || stopped || aiError) && (
          <div className="flex items-center justify-between gap-3 border-b border-border p-4 text-sm">
            <div className="min-w-0">
              {scanning && stats && <p className="truncate">Indexing {baseName(stats.root_path)}</p>}
              {!scanning && embedding && <p>Analyzing photos for search</p>}
              {!scanning && !embedding && stopped && stats && (
                <p className="truncate">
                  Indexing {baseName(stats.root_path)} stopped at {stats.processed.toLocaleString()} of{" "}
                  {stats.supported_images.toLocaleString()}
                </p>
              )}
              {!scanning && !embedding && aiError && <p className="text-destructive">{aiError}</p>}
              {scanning && stats?.current_file && (
                <p className="truncate text-xs text-muted-foreground" title={stats.current_file}>
                  {baseName(stats.current_file)}
                </p>
              )}
            </div>
            {(scanning || embedding) && (
              <Button size="sm" variant="ghost" onClick={onCancel}>
                Cancel
              </Button>
            )}
            {!scanning && !embedding && stopped && stats && (
              <Button size="sm" onClick={() => onScan(stats.root_path)}>
                Resume
              </Button>
            )}
          </div>
        )}

        {library && library.roots.length > 0 && (
          <ul className="max-h-60 overflow-y-auto py-1">
            {library.roots.map((root) => (
              <li key={root.id} className="flex items-center gap-3 px-4 py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm" title={root.path}>
                    {baseName(root.path)}
                  </p>
                  <p className="truncate text-xs text-muted-foreground" title={root.path}>
                    {root.path}
                  </p>
                </div>
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={scanning && stats?.root_path === root.path}
                  onClick={() => onScan(root.path)}
                >
                  Rescan
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  className="text-muted-foreground hover:text-destructive"
                  disabled={scanning && stats?.root_path === root.path}
                  onClick={() => onRemove(root)}
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        )}

        <div className="flex gap-2 border-t border-border p-3">
          <Button size="sm" variant="secondary" disabled={!hasBridge} onClick={onAddFolder} title={hasBridge ? undefined : "Available in the Cortex desktop app"}>
            Add folder…
          </Button>
          <Button size="sm" variant="ghost" onClick={onIndexComputer}>
            Index this computer
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function Dot({ tone }: { tone: Tone }) {
  return (
    <span
      aria-hidden
      className={cn(
        "size-1.5 shrink-0 rounded-full",
        tone === "busy" && "animate-pulse bg-star shadow-[0_0_8px_var(--star)]",
        tone === "ok" && "bg-muted-foreground/70",
        tone === "warn" && "bg-star",
        tone === "error" && "bg-destructive",
      )}
    />
  );
}
