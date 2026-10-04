export const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8000";

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

export const getImages = (limit: number, offset: number) =>
  request<ImagePage>(`/images?limit=${limit}&offset=${offset}`);

export const thumbnailUrl = (id: number) => `${BACKEND_URL}/images/${id}/thumbnail`;
