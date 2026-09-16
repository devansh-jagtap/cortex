"use client";

import { useEffect, useState } from "react";
import { checkBackendHealth, scanFolder, type ScanResponse } from "@/lib/backend";

type Status = "idle" | "selecting" | "scanning" | "done" | "error";

export default function Home() {
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [result, setResult] = useState<ScanResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const hasBridge = typeof window !== "undefined" && !!window.cortex;

  useEffect(() => {
    checkBackendHealth().then(setBackendOnline);
    const interval = setInterval(() => {
      checkBackendHealth().then(setBackendOnline);
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  async function handleSelectFolder() {
    setErrorMessage(null);

    if (!window.cortex) {
      setErrorMessage("Folder selection is only available inside the Cortex desktop app.");
      setStatus("error");
      return;
    }

    setStatus("selecting");
    const path = await window.cortex.selectFolder();

    if (!path) {
      setStatus("idle");
      return;
    }

    setSelectedPath(path);
    setStatus("scanning");

    try {
      const scanResult = await scanFolder(path);
      setResult(scanResult);
      setStatus("done");
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Scan failed.");
      setStatus("error");
    }
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center bg-neutral-950 px-6 text-neutral-50">
      <div className="flex w-full max-w-xl flex-col items-center gap-8 text-center">
        <div className="flex flex-col items-center gap-3">
          <h1 className="text-4xl font-semibold tracking-tight">CORTEX</h1>
          <p className="text-neutral-400">Search your files with AI</p>
        </div>

        <button
          onClick={handleSelectFolder}
          disabled={status === "selecting" || status === "scanning"}
          className="rounded-full bg-neutral-50 px-6 py-3 text-sm font-medium text-neutral-950 transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {status === "scanning" ? "Scanning…" : "Select Folder"}
        </button>

        <div className="flex items-center gap-2 text-xs text-neutral-500">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              backendOnline ? "bg-emerald-500" : "bg-red-500"
            }`}
          />
          {backendOnline === null
            ? "Checking backend…"
            : backendOnline
              ? "Backend connected"
              : "Backend offline"}
          {!hasBridge && " · running outside desktop shell"}
        </div>

        {errorMessage && (
          <p className="max-w-sm text-sm text-red-400">{errorMessage}</p>
        )}

        {result && (
          <div className="w-full rounded-xl border border-neutral-800 bg-neutral-900 p-6 text-left">
            <p className="mb-4 truncate text-sm text-neutral-400" title={selectedPath ?? undefined}>
              {selectedPath}
            </p>
            <dl className="grid grid-cols-3 gap-4">
              <div>
                <dt className="text-xs text-neutral-500">Total files</dt>
                <dd className="text-2xl font-semibold">{result.total_files}</dd>
              </div>
              <div>
                <dt className="text-xs text-neutral-500">Images found</dt>
                <dd className="text-2xl font-semibold text-emerald-400">
                  {result.supported_images}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-neutral-500">Other files</dt>
                <dd className="text-2xl font-semibold text-neutral-500">
                  {result.unsupported_files}
                </dd>
              </div>
            </dl>
            {result.error_count > 0 && (
              <p className="mt-4 text-xs text-amber-500">
                {result.error_count} item(s) could not be read and were skipped.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
