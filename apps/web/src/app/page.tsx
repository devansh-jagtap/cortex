"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";

import { AddFolderDialog } from "@/components/add-folder-dialog";
import { EntityHeader } from "@/components/entity";
import { PhotoGrid, type PhotoGridHandle } from "@/components/photo-grid";
import { PhotoViewer } from "@/components/photo-viewer";
import { SearchField } from "@/components/search-field";
import { StatusMenu, progressOf } from "@/components/status-menu";
import { Button } from "@/components/ui/button";
import { FolderPlusIcon } from "lucide-react";
import {
  cancelIndexing,
  checkBackendHealth,
  getEntity,
  getImages,
  getIndexStatus,
  getLibrary,
  getSuggestedRoots,
  removeFolder,
  searchPhotos,
  startIndexing,
  type EntityDetail,
  type ImageItem,
  type IndexStatus,
  type Library,
  type SearchResponse,
} from "@/lib/backend";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";

// MapLibre touches `window` when it loads, so it is only ever loaded in the browser.
const MapView = dynamic(() => import("@/components/map-view"), { ssr: false });
const GalaxyView = dynamic(() => import("@/components/galaxy-view"), { ssr: false });

type View = "search" | "map" | "galaxy";

const PAGE_SIZE = 120;
const RESULT_LIMIT = 120;
const SEARCH_DELAY_MS = 350;
// CLIP similarity isn't comparable across queries, so "relevant" is judged
// relative to the best hit: results close to it are shown first, the rest
// behind "Show more". A weak best hit means nothing really matches.
// Both numbers were tuned on real searches with ViT-B/32.
const CLOSE_TO_BEST = 0.07;
const WEAK_BEST_SCORE = 0.22;

export default function Home() {
  const [online, setOnline] = useState<boolean | null>(null);
  const [status, setStatus] = useState<IndexStatus>({ state: "idle" });
  const [library, setLibrary] = useState<Library | null>(null);
  const [photos, setPhotos] = useState<ImageItem[]>([]);
  const [photoTotal, setPhotoTotal] = useState(0);
  const [hasBridge, setHasBridge] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [viewer, setViewer] = useState<{ items: ImageItem[]; index: number } | null>(null);
  const [view, setView] = useState<View>("search");
  const [expandedQuery, setExpandedQuery] = useState<string | null>(null);
  const [entity, setEntity] = useState<EntityDetail | null>(null);
  const [addFolderOpen, setAddFolderOpen] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const gridRef = useRef<PhotoGridHandle>(null);
  const lastActivity = useRef<number | null | undefined>(undefined);
  const lastRefresh = useRef(0);
  const searchSeq = useRef(0);
  const searchAbort = useRef<AbortController | null>(null);
  const lastQuery = useRef("");

  const refreshLibrary = useCallback(async () => {
    lastRefresh.current = Date.now();
    const [lib, page] = await Promise.all([getLibrary(), getImages(PAGE_SIZE, 0)]);
    setLibrary(lib);
    setPhotos(page.items);
    setPhotoTotal(page.total);
  }, []);

  // One adaptive status loop: fast while Cortex is busy, slow when idle. The
  // watcher can change the library at any moment, so the grid refreshes
  // whenever the engine reports new activity.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const loop = async () => {
      try {
        const next = await getIndexStatus();
        if (cancelled) return;
        setHasBridge(!!window.cortex);
        setOnline(true);
        setStatus(next);
        const activityChanged = next.last_activity_at !== lastActivity.current;
        const longRunning = next.busy && Date.now() - lastRefresh.current > 3000;
        if (activityChanged || longRunning) {
          lastActivity.current = next.last_activity_at;
          await refreshLibrary();
        }
        timer = setTimeout(loop, next.busy ? 400 : 2000);
      } catch {
        if (cancelled) return;
        setOnline(await checkBackendHealth());
        timer = setTimeout(loop, 3000);
      }
    };
    loop();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [refreshLibrary]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement).closest("input, textarea, [contenteditable='true']");
      const slash = e.key === "/" && !typing;
      const commandK = e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey);
      if (slash || commandK) {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const runSearch = useCallback(async (text: string) => {
    const q = text.trim();
    if (q === lastQuery.current) return;
    lastQuery.current = q;
    searchAbort.current?.abort();
    if (!q) {
      setResults(null);
      setSearching(false);
      setSearchError(null);
      return;
    }
    const controller = new AbortController();
    searchAbort.current = controller;
    const seq = ++searchSeq.current;
    setSearching(true);
    setSearchError(null);
    try {
      const response = await searchPhotos(q, RESULT_LIMIT, controller.signal);
      if (seq === searchSeq.current) {
        setResults(response);
        setViewer(null);
      }
    } catch (err) {
      if (controller.signal.aborted || seq !== searchSeq.current) return;
      lastQuery.current = "";
      setSearchError(err instanceof Error ? err.message : "Search didn't work.");
    } finally {
      if (seq === searchSeq.current) setSearching(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => runSearch(query), query.trim() ? SEARCH_DELAY_MS : 0);
    return () => clearTimeout(timer);
  }, [query, runSearch]);

  async function beginIndexing(path: string) {
    setNotice(null);
    try {
      await startIndexing(path);
      setStatus(await getIndexStatus());
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Couldn't start indexing.");
    }
  }

  /** The desktop app opens Windows' folder picker; a plain browser asks for the path. */
  async function handleAddFolder() {
    if (!window.cortex) {
      setAddFolderOpen(true);
      return;
    }
    const path = await window.cortex.selectFolder();
    if (path) await beginIndexing(path);
  }

  async function addFolderByPath(path: string): Promise<string | null> {
    try {
      await startIndexing(path);
      setStatus(await getIndexStatus());
      return null;
    } catch (err) {
      return err instanceof Error ? err.message : "Couldn't add that folder.";
    }
  }

  async function handleIndexComputer() {
    try {
      const { home } = await getSuggestedRoots();
      const ok = window.confirm(
        `Index every photo in ${home}?\n\nThe first run can take a while. You can keep working; Cortex runs in the background and skips system and app folders.`,
      );
      if (ok) await beginIndexing(home);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Couldn't start indexing.");
    }
  }

  async function handleRemoveFolder(root: { id: number; path: string }) {
    const ok = window.confirm(
      `Remove ${root.path} from Cortex?

Its photos leave your Cortex library and searches. The folder and the photos in it stay exactly where they are.`,
    );
    if (!ok) return;
    setNotice(null);
    try {
      await removeFolder(root.id);
      setEntity(null);
      setResults(null);
      lastQuery.current = "";
      setQuery("");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Couldn't remove that folder.");
    }
  }

  async function handleLoadMore() {
    const page = await getImages(PAGE_SIZE, photos.length);
    setPhotos((prev) => [...prev, ...page.items]);
    setPhotoTotal(page.total);
  }

  /** Show one place, scene, or event: its photos and what it connects to. */
  async function openEntity(id: number) {
    try {
      const detail = await getEntity(id);
      searchAbort.current?.abort();
      lastQuery.current = "";
      setQuery("");
      setResults(null);
      setViewer(null);
      setView("search");
      setEntity(detail);
      window.scrollTo({ top: 0 });
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Couldn't open that.");
    }
  }

  // Results limited to a place without extra words carry no score: they are
  // all relevant, newest first, so none are held back behind "Show more".
  const best = results?.results[0]?.score;
  const closeCount = !results
    ? 0
    : best === undefined
      ? results.results.length
      : results.results.filter((r) => (r.score ?? 0) >= best - CLOSE_TO_BEST).length;
  const expanded = results !== null && expandedQuery === results.query;
  const items: ImageItem[] = entity
    ? entity.photos
    : results
      ? expanded
        ? results.results
        : results.results.slice(0, closeCount)
      : photos;
  const weak = results !== null && !results.place && best !== undefined && best < WEAK_BEST_SCORE;
  const hasLibrary = library !== null && library.roots.length > 0;
  const progress = progressOf(status);
  const analyzing = status.embedding?.state === "running";

  return (
    <div className="flex min-h-screen flex-1 flex-col">
      <header className="sticky top-0 z-20 border-b border-border/70 bg-background/85 backdrop-blur-md">
        <div className="mx-auto flex h-12 w-full max-w-[1240px] items-center justify-between gap-6 px-6">
          <div className="flex items-center gap-7">
            <span className="font-serif text-[22px] leading-none tracking-[-0.01em]">Cortex</span>
            {hasLibrary && (
              <nav aria-label="Views" className="flex items-center gap-1 text-sm">
                {(["search", "map", "galaxy"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setView(v)}
                    aria-current={view === v ? "page" : undefined}
                    className={cn(
                      "relative rounded-md px-2.5 py-1.5 capitalize transition-colors focus-visible:outline-2 focus-visible:outline-star",
                      view === v ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {v}
                    {view === v && <span aria-hidden className="absolute inset-x-2.5 -bottom-[9px] h-px bg-star" />}
                  </button>
                ))}
              </nav>
            )}
          </div>
          <div className="flex items-center gap-2">
          {online && (
            <Button variant="ghost" size="sm" onClick={handleAddFolder} className="text-muted-foreground hover:text-foreground">
              <FolderPlusIcon />
              Add folder
            </Button>
          )}
          <StatusMenu
            status={status}
            online={online}
            library={library}
            onAddFolder={handleAddFolder}
            onIndexComputer={handleIndexComputer}
            onScan={beginIndexing}
            onRemove={handleRemoveFolder}
            onCancel={() => void cancelIndexing()}
          />
          </div>
        </div>
        {progress !== null && (
          <div
            aria-hidden
            className="absolute bottom-[-1px] left-0 h-px bg-star shadow-[0_0_6px_var(--star)] transition-[width] duration-300"
            style={{ width: `${Math.max(2, progress * 100)}%` }}
          />
        )}
      </header>

      {hasLibrary && view === "map" && <MapView onOpen={(list, index) => setViewer({ items: list, index })} />}
      {hasLibrary && view === "galaxy" && <GalaxyView onOpen={(list, index) => setViewer({ items: list, index })} />}

      <main
        className={cn(
          "mx-auto w-full max-w-[1240px] flex-1 flex-col px-6 pb-20",
          view !== "search" && hasLibrary ? "hidden" : "flex",
        )}
      >
        {notice && (
          <p role="alert" className="mt-4 rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-2.5 text-sm text-destructive">
            {notice}
          </p>
        )}

        {online === false && <Offline />}

        {online && library && !hasLibrary && (
          <Welcome onIndexComputer={handleIndexComputer} onAddFolder={handleAddFolder} />
        )}

        {hasLibrary && (
          <>
            <section
              className={cn("transition-[padding] duration-300", results || entity ? "pt-8 pb-6" : "pt-[11vh] pb-10")}
            >
              <SearchField
                ref={inputRef}
                value={query}
                compact={!!results || !!entity}
                searching={searching}
                onChange={(value) => {
                  setEntity(null);
                  setQuery(value);
                }}
                onSubmit={() => {
                  lastQuery.current = "";
                  void runSearch(query);
                }}
                onPickExample={(example) => {
                  setEntity(null);
                  setQuery(example);
                  void runSearch(example);
                }}
                onLeaveDown={() => gridRef.current?.focusFirst()}
              />
              {entity ? (
                <div className="mt-7">
                  <EntityHeader entity={entity} onOpen={openEntity} onClose={() => setEntity(null)} />
                </div>
              ) : (
              <p className="mt-5 text-sm text-muted-foreground" aria-live="polite">
                {searchError ? (
                  <span className="text-destructive">Search didn&apos;t work: {searchError}</span>
                ) : searching && status.model?.state !== "ready" ? (
                  "Loading the AI model. The first search after Cortex starts takes a few seconds."
                ) : results?.place ? (
                  <>
                    <span className="text-foreground/90">
                      {plural(results.refined_by ? items.length : results.searched, "photo")} in{" "}
                      <button
                        type="button"
                        onClick={() => openEntity(results.place!.id)}
                        className="underline decoration-star/60 underline-offset-4 hover:decoration-star focus-visible:outline-2 focus-visible:outline-star"
                      >
                        {results.place.name}
                      </button>
                      {results.refined_by && ` closest to “${results.refined_by}”`}
                    </span>
                    <span className="ml-2 tabular-nums">{results.took_ms} ms</span>
                  </>
                ) : results ? (
                  results.searched === 0 ? (
                    "Search starts working once Cortex has analyzed your photos."
                  ) : (
                    <>
                      <span className="text-foreground/90">
                        {weak
                          ? `Nothing looks much like “${results.query}”. These are the nearest.`
                          : `${plural(items.length, "photo")} closest to “${results.query}”`}
                      </span>
                      <span className="ml-2 tabular-nums">{results.took_ms} ms</span>
                      {analyzing && <span className="ml-2">Still analyzing new photos, so results may improve.</span>}
                    </>
                  )
                ) : (
                  <>
                    <span className="text-foreground/90">
                      {plural(library.images_indexed, "photo")} from {plural(library.roots.length, "folder")}
                    </span>
                    {analyzing && <span className="ml-2">Analyzing photos for search…</span>}
                  </>
                )}
              </p>
              )}
            </section>

            {items.length > 0 && (
              <PhotoGrid
                revealKey={entity ? `entity:${entity.id}` : results ? `search:${results.query}` : "library"}
                ref={gridRef}
                items={items}
                onOpen={(index) => setViewer({ items, index })}
                onLeaveUp={() => inputRef.current?.focus()}
              />
            )}

            {entity && entity.photos_total > entity.photos.length && (
              <p className="mt-8 self-center text-sm text-muted-foreground">
                Showing the latest {entity.photos.length.toLocaleString()} of {entity.photos_total.toLocaleString()}.
              </p>
            )}

            {!entity && results && !expanded && results.results.length > items.length && (
              <Button variant="outline" className="mt-8 self-center" onClick={() => setExpandedQuery(results.query)}>
                Show {(results.results.length - items.length).toLocaleString()} more
              </Button>
            )}

            {!results && !entity && photos.length < photoTotal && (
              <Button variant="outline" className="mt-8 self-center" onClick={handleLoadMore}>
                Show {Math.min(PAGE_SIZE, photoTotal - photos.length).toLocaleString()} more
              </Button>
            )}
          </>
        )}
      </main>

      <AddFolderDialog open={addFolderOpen} onOpenChange={setAddFolderOpen} onAdd={addFolderByPath} />

      <PhotoViewer
        items={viewer?.items ?? []}
        index={viewer?.index ?? null}
        hasBridge={hasBridge}
        onIndexChange={(index) => setViewer((v) => (index === null || !v ? null : { ...v, index }))}
        onOpenEntity={openEntity}
      />
    </div>
  );
}

function Welcome({
  onIndexComputer,
  onAddFolder,
}: {
  onIndexComputer: () => void;
  onAddFolder: () => void;
}) {
  return (
    <section className="flex max-w-xl flex-1 flex-col justify-center gap-5 py-20">
      <h1 className="font-serif text-[44px] leading-[1.1] tracking-[-0.01em]">Find any photo by describing it.</h1>
      <p className="max-w-md text-[15px] leading-relaxed text-muted-foreground">
        Cortex looks at the photos on this computer and keeps watching for new ones, so you can search for “the
        whiteboard from Tuesday” instead of IMG_4821. Nothing leaves this computer.
      </p>
      <div className="flex flex-wrap gap-3 pt-2">
        <Button size="lg" onClick={onIndexComputer}>
          Index this computer
        </Button>
        <Button size="lg" variant="outline" onClick={onAddFolder}>
          Choose a folder
        </Button>
      </div>
    </section>
  );
}

function Offline() {
  return (
    <section className="flex max-w-xl flex-1 flex-col justify-center gap-3 py-20">
      <h1 className="font-serif text-[36px] leading-tight">The Cortex engine isn&apos;t running.</h1>
      <p className="text-[15px] leading-relaxed text-muted-foreground">
        Start it from the project folder with <code className="rounded bg-card px-1.5 py-0.5 text-foreground">npm run dev</code>.
        This page reconnects on its own.
      </p>
    </section>
  );
}
