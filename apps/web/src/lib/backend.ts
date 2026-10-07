// In the desktop app the engine's address comes from Electron, which starts
// it on a free port each launch; in a plain browser it's the dev default.
export const BACKEND_URL =
  (typeof window !== "undefined" && window.cortex?.backendUrl) ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "http://127.0.0.1:8000";

export type JobState = "idle" | "running" | "done" | "failed" | "cancelled" | "interrupted";

export interface IndexStats {
  root_path: string;
  total_files?: number;
  supported_images: number;
  unsupported_files?: number;
  processed: number;
  new_images?: number;
  changed_images?: number;
  unchanged_images?: number;
  moved_images?: number;
  removed_images?: number;
  placeholders?: number;
  failed: number;
  current_file: string | null;
  error_count?: number;
  errors?: string[];
}

export interface IndexStatus {
  job_id?: number;
  state: JobState;
  started_at?: number;
  finished_at?: number | null;
  error?: string | null;
  stats?: IndexStats;
  busy?: boolean;
  updating?: boolean;
  queued_scans?: number;
  last_activity_at?: number | null;
  watching?: number;
  embedding?: EmbeddingStatus;
  model?: ModelStatus;
  organizing?: { state: "idle" | "running" | "done" | "error"; errors?: string[] };
}

export interface EmbeddingStatus {
  state: "idle" | "running" | "done" | "cancelled" | "error";
  total?: number;
  processed?: number;
  failed?: number;
  error?: string | null;
}

export interface ModelStatus {
  state: "not_loaded" | "downloading" | "loading" | "ready" | "error";
  device: string | null;
  error: string | null;
}

export interface Library {
  images_indexed: number;
  images_with_gps: number;
  roots: { id: number; path: string; last_scanned_at: number | null }[];
}

export interface ImageItem {
  id: number;
  filename: string;
  path: string;
  size: number;
  width: number | null;
  height: number | null;
  captured_at: number | null;
  latitude: number | null;
  longitude: number | null;
  camera_make: string | null;
  camera_model: string | null;
}

export interface SearchResult extends ImageItem {
  /** Absent when results are simply everything in a place, newest first. */
  score?: number;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  took_ms: number;
  searched: number;
  /** Set when the query named a known place; results are limited to it. */
  place?: { id: number; name: string };
  /** What was left of the query after the place, used to rank within it. */
  refined_by?: string | null;
}

export type EntityType = "place" | "scene" | "event" | "person";

export interface EntitySummary {
  id: number;
  type: EntityType;
  name: string;
  level: "city" | "region" | "country" | null;
  data: { region?: string; country?: string; start?: number; end?: number; trip?: boolean };
}

export interface EntityDetail extends EntitySummary {
  photos_total: number;
  photos: ImageItem[];
  related: (EntitySummary & { kind: string; weight: number })[];
}

export interface ImageDetail extends ImageItem {
  entities: EntitySummary[];
}

export interface ImagePage {
  total: number;
  items: ImageItem[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : detail?.[0]?.msg;
    throw new Error(message ?? `Request failed (${res.status})`);
  }
  return res.json();
}

export async function checkBackendHealth(): Promise<boolean> {
  try {
    await request("/health");
    return true;
  } catch {
    return false;
  }
}

export const startIndexing = (path: string) =>
  request<{ job_id: number }>("/index/start", { method: "POST", body: JSON.stringify({ path }) });

export const cancelIndexing = () => request<{ cancelled: boolean }>("/index/cancel", { method: "POST" });

export const getIndexStatus = () => request<IndexStatus>("/index/status");

export const getLibrary = () => request<Library>("/library");

export const getSuggestedRoots = () => request<{ home: string }>("/roots/suggested");

export const removeFolder = (rootId: number) => request<{ removing: string }>(`/roots/${rootId}`, { method: "DELETE" });

export const getImages = (limit: number, offset: number) =>
  request<ImagePage>(`/images?limit=${limit}&offset=${offset}`);

export const getImagesByIds = (ids: number[]) => request<ImagePage>(`/images?limit=500&ids=${ids.join(",")}`);

export const getImageDetail = (id: number) => request<ImageDetail>(`/images/${id}/metadata`);

export const getEntity = (id: number) => request<EntityDetail>(`/entities/${id}`);

export interface PhotoNode {
  id: number;
  filename: string;
  width: number | null;
  height: number | null;
  cluster: number;
}

export interface PhotoLink {
  source: number;
  target: number;
  similarity: number;
}

export interface PhotoGraph {
  nodes: PhotoNode[];
  edges: PhotoLink[];
  clusters: { id: number; size: number; label: string | null }[];
  total: number;
}

/** Photos joined when they look alike; `focus` = one photo and its closest look-alikes. */
export const getPhotoGraph = (options: { limit?: number; focus?: number } = {}) => {
  const params = new URLSearchParams();
  if (options.limit) params.set("limit", String(options.limit));
  if (options.focus !== undefined) params.set("focus", String(options.focus));
  return request<PhotoGraph>(`/graph/photos?${params}`);
};

export const searchPhotos = (query: string, limit: number, signal?: AbortSignal) =>
  request<SearchResponse>("/search", {
    method: "POST",
    body: JSON.stringify({ query, limit }),
    signal,
  });

export const thumbnailUrl = (id: number) => `${BACKEND_URL}/images/${id}/thumbnail`;

export const originalUrl = (id: number) => `${BACKEND_URL}/images/${id}/original`;
