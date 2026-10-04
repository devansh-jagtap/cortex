"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelIndexing,
  checkBackendHealth,
  getImages,
  getIndexStatus,
  getLibrary,
  startIndexing,
  thumbnailUrl,
  type ImageItem,
  type IndexStatus,
  type Library,
} from "@/lib/backend";

const PAGE_SIZE = 120;

export default function Home() {
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [status, setStatus] = useState<IndexStatus>({ state: "idle" });
  const [library, setLibrary] = useState<Library | null>(null);
  const [images, setImages] = useState<ImageItem[]>([]);
  const [totalImages, setTotalImages] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [hasBridge, setHasBridge] = useState(false);
  const wasRunning = useRef(false);

  const refreshLibrary = useCallback(async () => {
    const [lib, page] = await Promise.all([getLibrary(), getImages(PAGE_SIZE, 0)]);
    setLibrary(lib);
    setImages(page.items);
    setTotalImages(page.total);
  }, []);

  useEffect(() => {
    const tick = async () => {
      setHasBridge(!!window.cortex);
      const online = await checkBackendHealth();
      setBackendOnline(online);
      if (online && library === null) {
        await refreshLibrary().catch(() => undefined);
        setStatus(await getIndexStatus());
      }
    };
    tick();
    const interval = setInterval(tick, 5000);
    return () => clearInterval(interval);
  }, [library, refreshLibrary]);

  const running = status.state === "running";

  useEffect(() => {
    if (!running) return;
    wasRunning.current = true;
    const interval = setInterval(async () => {
      try {
        const next = await getIndexStatus();
        setStatus(next);
        if (next.state !== "running" && wasRunning.current) {
          wasRunning.current = false;
          await refreshLibrary();
        }
      } catch {
        // backend briefly unreachable; the health check will surface it
      }
    }, 400);
    return () => clearInterval(interval);
  }, [running, refreshLibrary]);

  async function beginIndexing(path: string) {
    setErrorMessage(null);
    try {
      await startIndexing(path);
      setStatus(await getIndexStatus());
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Could not start indexing.");
    }
  }

  async function handleAddFolder() {
    if (!window.cortex) {
      setErrorMessage("Folder selection is only available inside the Cortex desktop app.");
      return;
    }
    const path = await window.cortex.selectFolder();
    if (path) await beginIndexing(path);
  }

  async function handleLoadMore() {
    const page = await getImages(PAGE_SIZE, images.length);
    setImages((prev) => [...prev, ...page.items]);
    setTotalImages(page.total);
  }

  const isEmpty = library !== null && library.images_indexed === 0 && library.roots.length === 0;

  return (
    <div className="flex min-h-screen flex-1 flex-col bg-neutral-950 text-neutral-50">
      <header className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-neutral-900 bg-neutral-950/90 px-6 py-4 backdrop-blur">
        <div className="flex items-baseline gap-3">
          <h1 className="text-lg font-semibold tracking-tight">CORTEX</h1>
          <span className="hidden text-sm text-neutral-500 sm:inline">Search your files with AI</span>
        </div>
        <div className="flex items-center gap-4">
          <BackendDot online={backendOnline} hasBridge={hasBridge} />
          <button
            onClick={handleAddFolder}
            disabled={running || !backendOnline}
            className="rounded-full bg-neutral-50 px-4 py-2 text-sm font-medium text-neutral-950 transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Add folder
          </button>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-6 py-6">
        {errorMessage && (
          <p role="alert" className="rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-300">
            {errorMessage}
          </p>
        )}

        <JobPanel
          status={status}
          onCancel={() => cancelIndexing()}
          onResume={(path) => beginIndexing(path)}
        />

        {isEmpty && !running && (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 py-24 text-center">
            <p className="text-2xl font-semibold tracking-tight">Your library is empty</p>
            <p className="max-w-sm text-sm text-neutral-400">
              Add a folder and Cortex will catalog every photo in it. Your files are never moved,
              renamed, or uploaded.
            </p>
          </div>
        )}

        {library && library.roots.length > 0 && (
          <section className="flex flex-col gap-2">
            <div className="flex items-baseline justify-between">
              <h2 className="text-sm font-medium text-neutral-300">
                {library.images_indexed.toLocaleString()} photos indexed
                {library.images_with_gps > 0 && (
                  <span className="text-neutral-500"> · {library.images_with_gps.toLocaleString()} with location</span>
                )}
              </h2>
            </div>
            <ul className="flex flex-col divide-y divide-neutral-900 rounded-xl border border-neutral-900">
              {library.roots.map((root) => (
                <li key={root.id} className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm">
                  <span className="truncate text-neutral-300" title={root.path}>
                    {root.path}
                  </span>
                  <span className="flex shrink-0 items-center gap-3">
                    <span className="text-xs text-neutral-500">
                      {root.last_scanned_at ? `Scanned ${formatRelative(root.last_scanned_at)}` : "Not finished"}
                    </span>
                    <button
                      onClick={() => beginIndexing(root.path)}
                      disabled={running}
                      className="rounded-full border border-neutral-700 px-3 py-1 text-xs text-neutral-200 transition-colors hover:bg-neutral-900 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      Rescan
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {images.length > 0 && (
          <section className="flex flex-col gap-4">
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-2">
              {images.map((image) => (
                <li
                  key={image.id}
                  className="group relative aspect-square overflow-hidden rounded-lg bg-neutral-900"
                  title={`${image.filename}${image.width ? ` · ${image.width}×${image.height}` : ""}`}
                >
                  {/* Plain <img>: thumbnails come from the local backend; next/image's optimizer adds nothing here. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={thumbnailUrl(image.id)}
                    alt={image.filename}
                    loading="lazy"
                    decoding="async"
                    className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                  />
                  <span className="pointer-events-none absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/80 to-transparent px-2 pb-1.5 pt-6 text-[11px] text-neutral-200 opacity-0 transition-opacity group-hover:opacity-100">
                    {image.filename}
                  </span>
                </li>
              ))}
            </ul>
            {images.length < totalImages && (
              <button
                onClick={handleLoadMore}
                className="self-center rounded-full border border-neutral-700 px-4 py-2 text-sm text-neutral-200 hover:bg-neutral-900"
              >
                Load more ({(totalImages - images.length).toLocaleString()} left)
              </button>
            )}
          </section>
        )}
      </main>
    </div>
  );
}

function BackendDot({ online, hasBridge }: { online: boolean | null; hasBridge: boolean }) {
  return (
    <span className="flex items-center gap-2 text-xs text-neutral-500">
      <span className={`h-1.5 w-1.5 rounded-full ${online ? "bg-emerald-500" : online === null ? "bg-neutral-600" : "bg-red-500"}`} />
      {online === null ? "Connecting…" : online ? "Backend connected" : "Backend offline"}
      {!hasBridge && <span className="hidden md:inline"> · browser preview</span>}
    </span>
  );
}

function JobPanel({
  status,
  onCancel,
  onResume,
}: {
  status: IndexStatus;
  onCancel: () => void;
  onResume: (path: string) => void;
}) {
  const stats = status.stats;
  if (!stats || status.state === "idle") return null;

  const total = stats.supported_images;
  const pct = total > 0 ? Math.min(100, Math.round((stats.processed / total) * 100)) : 0;
  const folder = stats.root_path;

  if (status.state === "running") {
    return (
      <section aria-live="polite" className="flex flex-col gap-3 rounded-xl border border-neutral-800 bg-neutral-900 p-5">
        <div className="flex items-baseline justify-between gap-4">
          <p className="text-sm font-medium">
            Indexing{" "}
            <span className="tabular-nums">
              {stats.processed.toLocaleString()} / {total.toLocaleString()}
            </span>
          </p>
          <button onClick={onCancel} className="text-xs text-neutral-400 hover:text-neutral-100">
            Cancel
          </button>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-neutral-800">
          <div className="h-full rounded-full bg-emerald-500 transition-[width] duration-300" style={{ width: `${pct}%` }} />
        </div>
        <p className="truncate text-xs text-neutral-500" title={stats.current_file ?? folder}>
          {stats.current_file ? baseName(stats.current_file) : `Looking for photos in ${folder}…`}
        </p>
      </section>
    );
  }

  if (status.state === "interrupted" || status.state === "cancelled" || status.state === "failed") {
    const label =
      status.state === "failed"
        ? `Indexing failed${status.error ? `: ${status.error}` : ""}`
        : status.state === "cancelled"
          ? "Indexing was cancelled"
          : "Indexing was interrupted when Cortex closed";
    return (
      <section className="flex items-center justify-between gap-4 rounded-xl border border-amber-900/50 bg-amber-950/20 p-4">
        <div className="min-w-0">
          <p className="text-sm text-amber-200">{label}</p>
          <p className="truncate text-xs text-neutral-500">
            {stats.processed.toLocaleString()} of {total.toLocaleString()} photos done · {folder}
          </p>
        </div>
        <button
          onClick={() => onResume(folder)}
          className="shrink-0 rounded-full bg-neutral-50 px-4 py-1.5 text-xs font-medium text-neutral-950 hover:opacity-90"
        >
          Resume
        </button>
      </section>
    );
  }

  const parts = [
    stats.new_images && `${stats.new_images.toLocaleString()} new`,
    stats.changed_images && `${stats.changed_images.toLocaleString()} updated`,
    stats.moved_images && `${stats.moved_images.toLocaleString()} moved`,
    stats.removed_images && `${stats.removed_images.toLocaleString()} removed`,
    stats.unchanged_images && `${stats.unchanged_images.toLocaleString()} unchanged`,
  ].filter(Boolean);

  return (
    <section className="flex flex-col gap-1 rounded-xl border border-neutral-900 p-4 text-sm">
      <p className="text-neutral-300">
        Finished {folder && <span className="text-neutral-500">{baseName(folder)}</span>}
        {parts.length > 0 && <span className="text-neutral-500"> · {parts.join(" · ")}</span>}
      </p>
      {(stats.failed > 0 || (stats.placeholders ?? 0) > 0) && (
        <p className="text-xs text-amber-400">
          {stats.failed > 0 && `${stats.failed} file(s) couldn't be read. `}
          {(stats.placeholders ?? 0) > 0 &&
            `${stats.placeholders} online-only OneDrive file(s) were skipped to avoid downloading them.`}
        </p>
      )}
    </section>
  );
}

function baseName(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

function formatRelative(unixSeconds: number) {
  const diff = Date.now() / 1000 - unixSeconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return new Date(unixSeconds * 1000).toLocaleDateString();
}
