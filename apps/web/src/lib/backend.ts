const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8000";

export interface ScanResponse {
  root_path: string;
  total_files: number;
  supported_images: number;
  unsupported_files: number;
  error_count: number;
  errors: string[];
  sample_images: string[];
}

export async function checkBackendHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND_URL}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

export async function scanFolder(path: string): Promise<ScanResponse> {
  const res = await fetch(`${BACKEND_URL}/scan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail?.[0]?.msg ?? `Scan failed (${res.status})`);
  }

  return res.json();
}
