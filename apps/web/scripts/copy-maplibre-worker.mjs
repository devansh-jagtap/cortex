// MapLibre runs its tile work in a Web Worker that it loads from a URL next
// to its own script. After bundling that URL doesn't exist, so the worker
// and the chunk it shares are served as static files from public/maplibre/
// instead, copied from node_modules so they always match the installed version.
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const dist = join(dirname(createRequire(import.meta.url).resolve("maplibre-gl/package.json")), "dist");
const out = join(import.meta.dirname, "..", "public", "maplibre");
mkdirSync(out, { recursive: true });
for (const file of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
  copyFileSync(join(dist, file), join(out, file));
}
