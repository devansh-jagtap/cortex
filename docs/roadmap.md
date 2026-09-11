# Cortex — Development Roadmap

Ordered by dependency and risk. Every milestone leaves a working,
demonstrable product. A milestone is "done" only when it is run, tested,
and verified in the real app — never on the strength of code that
"should" work.

Legend: ✅ done · 🔨 next · ⏳ planned

---

## Phase 1 — Photo search MVP

### ✅ M1 · Foundation
Electron shell (secure preload bridge) + Next.js UI + FastAPI, wired end
to end. Select a folder → backend scans it → counts shown.
*Proves:* the three processes communicate; the security model holds.

### 🔨 M2 · Persistent, incremental index
- SQLite schema + migrations (`roots`, `files`, `image_metadata`,
  `index_jobs`), `storage/` layout.
- Change detection: size+mtime → BLAKE2b hash → new / unchanged /
  modified / moved / missing.
- Image extractor: dimensions, orientation, EXIF (date, GPS, camera),
  thumbnail generation with `Image.draft()` decoding.
- Background job runner (one worker thread), `POST /index/start`,
  `GET /index/status`, resumable after a crash.
- OneDrive placeholder detection.
- UI: indexing progress (`1,284 / 4,932`, current file), "Rescan".
- Tests: scanner, hashing, EXIF (synthetic images), migrations,
  resumability.

*Working product:* add a folder, Cortex catalogs every photo with
metadata and thumbnails; adding it again is instant; a killed job resumes.
*Why before AI:* every later stage writes into this schema; getting
incremental + resumable right on cheap operations (EXIF) is far easier
than debugging it on a 30-minute embedding run.

### ⏳ M3 · Semantic search core (backend only)
- `Embedder` interface + deterministic fake for tests.
- OpenCLIP ViT-B/32 embedder, lazy singleton, device auto-select,
  `GET /models/status`.
- `embeddings` table + FAISS `IndexFlatIP`/`IDMap2` store with
  rebuild-from-SQLite on startup mismatch.
- Embed stage in the pipeline (batched), vector removal on missing files.
- `POST /search` → ranked results with scores.
- Tests: FAISS store, search with fake embedder, one slow real-model
  test; measure RSS and images/sec on this machine.

*Working product:* `curl /search '{"query":"moon"}'` returns the right
photos. The central idea is proven before a single pixel of results UI.

### ⏳ M4 · Search UI (the MVP the product brief describes)
- shadcn/ui + Motion set up (first real UI milestone).
- Large search box with example prompts, "N photos indexed".
- Virtualised thumbnail grid (`GET /images/{id}/thumbnail`).
- Detail view: image, filename, path, dimensions, size, captured date,
  GPS, score; **Open Original** via id-based IPC (`window.cortex.openFile`).
- Empty / loading / error / "model downloading" states. Keyboard: `/` to
  focus search, arrows in grid, `Esc` to close.
- Dark theme first, light theme via tokens.

*Working product:* the demo — type "dogs at the beach", see the photos,
open one.

### ⏳ M5 · Map
- `GET /map/points` (server-side clustering above a threshold).
- MapLibre GL + OpenStreetMap tiles; markers/clusters → click → grid.
- Navigation: Search · Map.

*Working product:* every GPS-tagged photo on a map, explorable.

### ⏳ M6 · Robustness & performance
- Rescan semantics hardened (moved/renamed/deleted), "Remove folder".
- Optional `watchdog` file watcher.
- GPU switch documented and tested; batch size auto-tune.
- Benchmark on 10k+ photos; virtualisation and pagination tuned on real
  numbers, not guesses.

### ⏳ M7 · Packaging
- Next.js `output: 'export'`; Electron loads the static build.
- Electron spawns/supervises the backend; per-launch auth token.
- PyInstaller bundle for the backend; electron-builder installer.

*Working product:* an installer a friend can run.

---

## Phase 2 — Understanding & relationships

Each item is its own milestone; order is by "value ÷ risk".

1. **Places** — offline reverse geocoding of GPS → `entities(place)`.
   Search understands "photos from Goa".
2. **Timeline view** — histogram by `captured_at`, drill into a month.
3. **Events** — time+place clustering → `entities(event)`, auto-named,
   renameable. Timeline gains event lanes.
4. **Clusters** — embedding clustering + CLIP zero-shot labels → the
   "Moon · 67 photos" cards.
5. **Similar images** — "more like this" using the existing index.
6. **People** — local face detection/embedding, clustered, user-named,
   opt-in. Search understands "Rahul".
7. **Graph view** — force-directed projection of `entities` /
   `file_entities` / `entity_relations` with thumbnail nodes.
8. **Compound query understanding** — "Rahul at the beach in Goa in
   2024" → person ∩ place ∩ time + scene ranking, fused via RRF.

## Phase 3 — Multimodal

9. **Documents** (PDF, DOCX, TXT) — text extraction, chunking, a local
   text-embedding space, cross-space fusion in search.
10. **OCR** on images — text inside photos joins the text space.
11. **Audio** — Whisper transcription → text space + timestamps.
12. **Video** — keyframes → CLIP space; audio track → Whisper.
13. **Cross-file relationships** — the "Goa Trip contains the hotel
    receipt" links, from shared events/places/time.
14. **Local RAG / natural-language exploration** — a local LLM over the
    index, strictly after retrieval quality is proven.

---

## Guiding rules for every milestone

1. Inspect the repo. 2. Plan the smallest correct change. 3. Implement.
4. Run it. 5. Test it. 6. Fix what broke. 7. Verify in the real app.
8. Update the docs. 9. Commit with a clear message. 10. Only then, next.
