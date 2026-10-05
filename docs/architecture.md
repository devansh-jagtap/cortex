# Cortex — Architecture

This document is the technical design for Cortex. It is written to be read
by someone learning the system, so every major decision comes with the
*why*, not just the *what*. When a decision is a trade-off, the alternative
we rejected is named so you can judge it yourself.

Companion documents:

- [`roadmap.md`](roadmap.md) — the build order, milestone by milestone.
- [`development.md`](development.md) — how to run and test it.

---

## 0. The one-paragraph version

Cortex is three cooperating processes on one machine. **Electron** owns the
window and the only door to the OS. **Next.js/React** renders the UI and
never touches the filesystem. **Python/FastAPI** does everything heavy:
scanning folders, extracting metadata, running local AI models, storing
vectors in FAISS, and storing everything else in SQLite. **SQLite is the
single source of truth; FAISS and thumbnails are caches that can be rebuilt
from it.** Every visual view (search, grid, map, timeline, graph) is a
read-only projection over that same database, so adding a view never
touches the indexing pipeline.

---

## 1. Overall architecture

```
 ┌──────────────────────────────────────────────────────────────────┐
 │  ELECTRON MAIN PROCESS  (Node.js, trusted)                       │
 │   • window lifecycle      • native folder picker                 │
 │   • "open original file"  • (later) spawns & supervises backend  │
 └───────────────▲──────────────────────────────────────────────────┘
                 │ IPC via contextBridge — tiny, explicit API only
 ┌───────────────┴──────────────────────────────────────────────────┐
 │  RENDERER  (Next.js / React / TypeScript / Tailwind / shadcn)     │
 │   • search box, results grid, detail view, progress, map…        │
 │   • NO Node, NO filesystem, NO raw paths used for loading         │
 └───────────────▲──────────────────────────────────────────────────┘
                 │ HTTP, loopback only (127.0.0.1:8000)
 ┌───────────────┴──────────────────────────────────────────────────┐
 │  PYTHON BACKEND  (FastAPI)                                        │
 │                                                                   │
 │   API layer ─── search service ─── indexing pipeline (bg worker)  │
 │                      │                    │                       │
 │              ┌───────┴───────┐    ┌───────┴────────┐              │
 │              │ FAISS (cache) │    │ extractors      │              │
 │              └───────┬───────┘    │  image → EXIF,  │              │
 │                      │            │  thumbnail,     │              │
 │              ┌───────┴───────┐    │  CLIP embedding │              │
 │              │ SQLite (truth)│◄───┴────────────────┘              │
 │              └───────────────┘                                    │
 │                      ▲                                            │
 │              local filesystem (read-only, never modified)         │
 └──────────────────────────────────────────────────────────────────┘
```

**Why three processes instead of one?**
The AI ecosystem (PyTorch, OpenCLIP, FAISS, Pillow) lives in Python. The
desktop/UI ecosystem lives in JavaScript. Trying to do AI in Node (ONNX
runtime etc.) is possible but you fight the ecosystem at every step;
trying to do a premium desktop UI in Python is worse. So we let each
runtime do what it's best at and connect them with the simplest thing
that works: HTTP on localhost.

**Why HTTP and not a fancier IPC to Python?**
Because it is boring, debuggable with `curl`, testable without Electron,
and lets the UI be developed in a normal browser tab. We will only add
WebSockets if polling turns out to be genuinely inadequate (it won't be
for indexing progress).

---

## 2. What runs where

| Concern | Electron main | Renderer | Python |
|---|---|---|---|
| Window, menus, lifecycle | ✅ | | |
| Native folder picker | ✅ | | |
| Open original file in OS | ✅ (by **file id**, see §5) | | |
| Rendering UI, animations, state | | ✅ | |
| Loading thumbnails | | via `<img src="http://127.0.0.1:8000/…">` | ✅ serves them |
| Folder scanning, hashing | | | ✅ |
| EXIF, dimensions, thumbnails | | | ✅ |
| AI models (CLIP, later Whisper, faces) | | | ✅ |
| Vector index (FAISS) | | | ✅ |
| Metadata DB (SQLite) | | | ✅ |
| Background jobs, progress | | polls | ✅ |
| Spawning the backend in a packaged app | ✅ (later) | | |

The rule: **the renderer is untrusted web content**. It gets exactly the
capabilities it needs through `window.cortex.*` (defined in
`apps/desktop/electron/preload.ts`) and nothing else. `contextIsolation:
true`, `nodeIntegration: false`, `sandbox: true` are non-negotiable and
we never loosen them to "make something easier".

---

## 3. Data model

### 3.1 The core principle: SQLite is truth, everything else is a cache

Embeddings are stored **in SQLite as BLOBs** *and* in FAISS. FAISS is the
fast query structure; SQLite is the durable record. If the FAISS file is
missing, corrupt, or out of sync, it is rebuilt from SQLite in seconds
(100k × 512 floats ≈ 200 MB, a trivial in-memory load). Thumbnails are the
same: files on disk keyed by content hash, regenerable from originals.

**Why?** Crash recovery and consistency become almost free. There is one
place that must be transactionally correct (SQLite, which is very good at
that), and the fragile things (an in-memory vector index that has to be
flushed to disk) become disposable.

### 3.2 Tables for the photo MVP

```
roots            folders the user asked Cortex to index
  id, path (unique), added_at, last_scanned_at

files            one row per discovered file, any modality
  id, root_id, path (unique), filename, extension, kind ('image'),
  size, mtime_ns, content_hash,
  status ('discovered' | 'indexed' | 'error' | 'missing'),
  error, first_seen_at, indexed_at

image_metadata   image-specific facts (1:1 with files where kind='image')
  file_id (pk), width, height, orientation, captured_at,
  latitude, longitude, camera_make, camera_model, thumbnail_path

embeddings       one vector per (file, unit, space)
  id (pk = the FAISS id), file_id, unit (int, 0 for whole-image),
  space ('clip-vit-b-32'), vector (BLOB float32), created_at
  unique(file_id, unit, space)

index_jobs       persisted progress so a crash is resumable
  id, root_id, state, total, processed, failed, current_file,
  started_at, finished_at, error
```

Design notes worth understanding:

- **`files` is generic, `image_metadata` is specific.** When PDFs arrive we
  add `document_metadata`, not a new `documents` table with duplicated
  path/size/hash columns. Search results, the timeline, and the graph all
  join on `files.id` regardless of modality.
- **`embeddings.space`** names the model that produced the vector. Vectors
  from different models are *not comparable* (a CLIP vector and a text
  embedding model's vector live in different geometries). Keeping the space
  explicit means we can add a better image model later, re-embed side by
  side, and cut over — or run two spaces at once and fuse results (§7).
- **`embeddings.unit`** exists so that later a PDF can have 40 chunks and a
  video 200 keyframes, each with its own vector, all pointing at one file.
  For images it is always 0. Getting this shape right now costs nothing;
  migrating it later costs a lot.
- **`embeddings.id` is the FAISS id.** We use FAISS's `IndexIDMap2` so ids
  are explicit and vectors can be removed by id. We never rely on "the
  Nth vector added is file N".
- **Thumbnails are files on disk** (`storage/thumbnails/ab/abcdef….jpg`,
  keyed by content hash) — never binary in SQLite. Hash-keyed means
  duplicate photos share a thumbnail and a moved photo keeps its thumbnail.

### 3.3 Change detection (incremental indexing)

```
for each file on disk:
    row = files[path]
    if row is None:                          → NEW: hash, extract, embed
    elif (size, mtime_ns) == (row.size, row.mtime_ns):
                                             → UNCHANGED: skip entirely
    else:                                    → hash it
        if hash == row.content_hash:         → touched but same bytes: update mtime only
        else:                                → MODIFIED: re-extract, re-embed, replace vector

for each row whose path is no longer on disk:
    if some NEW file has the same content_hash:
                                             → MOVED/RENAMED: update path, keep everything
    else:                                    → MISSING: mark status, remove vector from FAISS
```

**Why size+mtime first, hash second?** Hashing 20 MB per photo × 50,000
photos means reading a terabyte. `stat()` is ~free. So we only hash what
*might* have changed, and only embed what *did* change. Move/rename
detection falls out of hashing for free and saves re-running the model.

We use BLAKE2b (in `hashlib`) rather than SHA-256: same security
properties for our purpose (identity, not cryptography), noticeably faster
in pure CPython.

---

## 4. Indexing architecture

### 4.1 Pipeline

```
 discover ──► diff ──► extract (EXIF, dims, thumbnail) ──► embed (batched) ──► commit
   walk        §3.3      Pillow, piexif                      CLIP, batch 16-32     SQLite + FAISS
```

Per file, the *extract* stage is independent; the *embed* stage is batched
because a model forward pass on 32 images is far cheaper than 32 passes on
1 image. The commit for a batch writes the `embeddings` rows and flips
`files.status` to `indexed` **in one SQLite transaction**, then adds the
vectors to FAISS. If the process dies between those two steps, startup
reconciliation (§4.4) repairs FAISS from SQLite.

### 4.2 Background execution

Indexing runs on **one background thread inside the FastAPI process** with
a job queue (one job at a time). The API thread stays free to answer
`/index/status` and `/search`.

**Why a thread, not asyncio?** The work is CPU/GPU bound (image decoding,
model inference). `asyncio` only helps with I/O waiting; a long model call
would block the event loop. Pillow and PyTorch release the GIL during
their heavy work, so a thread gives real concurrency with the API.

**Why not a separate process or a task queue (Celery/Redis)?** This machine
has ~8 GB RAM. PyTorch + CLIP loaded once costs ~1 GB; loading it in two
processes doubles that for no benefit. A queue broker is an operational
dependency we don't need for "one user, one job at a time". If we ever
need parallel *extraction* (not embedding), a small process pool for the
Pillow stage is the right upgrade, not a broker.

### 4.3 Resource discipline (this matters on 8 GB)

- The model is loaded **lazily on first use**, not at startup, and there is
  exactly one instance (a singleton behind a lock). *As built:* once the
  library has vectors, the worker warms the model in the background right
  after startup (load + one throwaway query, ~15 s). Measured: without it
  the first search after launch took 17.8 s; with it, 141 ms. The model is
  needed for every new photo anyway, so this costs no extra memory in
  practice.
- Images are decoded with `Image.draft()` for JPEGs — the decoder itself
  downsamples during decode, so a 24-megapixel photo never exists in RAM at
  full size when all we need is 224×224 for CLIP and 320px for a thumbnail.
  This is the single biggest memory/speed win in the whole pipeline.
- `Image.MAX_IMAGE_PIXELS` stays enabled (decompression-bomb protection);
  oversized files are recorded as `error`, not crashed on.
- Batch size: 16 on CPU, 32 on GPU, fp16 on GPU. Never hold more than one
  batch of decoded tensors.
- Every per-file step is wrapped: one corrupt file → `status='error'` with
  the message, and the job continues.

### 4.4 Crash recovery & startup reconciliation

On backend start:
1. Open SQLite, run pending schema migrations.
2. Build the in-memory FAISS index from the `embeddings` BLOBs. *As built
   (M4):* rather than persisting a `.faiss` file and comparing it with
   SQLite, the index is simply rebuilt from SQLite at startup and then
   reconciled after every change (by file id + embedding timestamp). Nothing
   can drift because there is no second copy on disk. Measured cost: 428
   vectors load instantly; at ~100k vectors this is ~200 MB of reads, which
   we will measure before adding a persisted file.
3. *(Folded into step 2.)*
4. Any `index_jobs` left in `running` are marked `interrupted`; the UI
   offers "Resume", which is simply "run the job again" — everything
   already `indexed` is skipped by §3.3.

### 4.5 File changes over time

MVP: rescans happen when the user clicks "Rescan" or adds a root, and
automatically on app start. A filesystem watcher (`watchdog`) is a later
milestone — it's a nice-to-have, and on Windows watchers over large trees
have edge cases we don't want to debug before search even works.

**OneDrive warning (relevant on this machine):** folders under OneDrive
may contain *Files On-Demand* placeholders — a file that looks present but
is actually in the cloud. Reading it triggers a download (or fails
offline). The scanner checks the Windows `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`
attribute and records such files as `status='error', error='cloud
placeholder'` instead of silently pulling gigabytes down.

---

## 5. Secure image display

The renderer must never load `file:///D:/Photos/…` directly — that requires
disabling Electron's web security, which we refuse to do.

Instead the backend serves images **by id**:

```
GET /images/{id}/thumbnail   → JPEG from storage/thumbnails (fast, small)
GET /images/{id}/original    → streams the original file (detail view only)
```

The renderer only ever *requests* by id. It does show paths (you want to
see exactly where a photo lives), but a path never appears in a URL and is
never sent anywhere, so there is no path-traversal surface. "Open Original"
works the same way: the renderer calls `window.cortex.openImage(id)` (or
`showImageInFolder(id)`); Electron main checks the call came from the Cortex
page, asks the backend for the path of that id, checks it is an existing
file with an image extension, then calls `shell.openPath`. The renderer
never hands a path to Electron. *As built (M5):* verified over CDP that the
bridge exposes only these three methods, that the page has no Node access,
and that non-integer, negative, and unknown ids are refused.

The backend binds to `127.0.0.1` only. Any process on the machine running
as the same user could hit it — but that process could also just read the
photos directly, so this adds no new exposure. Web pages open in the user's
browser can also send requests to `127.0.0.1`: CORS stops them from reading
any response or sending JSON POSTs (which need a preflight), but a page
could still *display* (not read) a thumbnail with an `<img>` tag. The fix is
a per-launch token that Electron generates and the backend requires; it
lands with packaging (M10), when Electron starts the backend itself.

---

## 6. Local AI models

### 6.1 Which model, and why

**OpenCLIP ViT-B/32, pretrained `laion2b_s34b_b79k`.**

CLIP is the reason Cortex can work at all: it maps *images and text into
the same 512-dimensional space*, trained so that a photo and a caption
describing it land close together. That gives us text→image search with
zero labels, zero training, and no per-user setup. "dogs at the beach" is
encoded by the text tower; every photo was encoded by the image tower;
nearest neighbours are the answer.

Why ViT-B/32 specifically: ~600 MB download, runs at usable speed on CPU
(~10–20 images/s) and very fast on a modest GPU, good zero-shot retrieval
quality. ViT-L/14 or SigLIP are better but 3–5× heavier — they are an
*upgrade path* (new `space`), not a starting point.

### 6.2 Managing models

- Weights are cached under `models/` (we point OpenCLIP's cache there) so
  they are downloaded once and never committed. *As built:* 578 MB, about
  two minutes on the first run; override the location with
  `CORTEX_MODELS_DIR`.
- *Windows Smart App Control:* on machines where it is enforced, it refuses
  to load native DLLs that are too new to have a reputation. `faiss-cpu`
  1.13+ is blocked on the development machine; 1.12.0 loads and is pinned.
  PyTorch, NumPy and OpenCLIP load normally.
- `GET /models/status` reports `{downloaded, loaded, device}` so the UI can
  show a first-run "Downloading model (600 MB)…" state honestly instead of
  a spinner that hangs.
- Device selection is automatic (`cuda` if available, else `cpu`); the
  code is device-agnostic. We start with the **CPU PyTorch wheel** (~200 MB)
  to keep setup risk low; switching to CUDA is one documented `pip` command
  and gives roughly 10× indexing throughput on the GTX 1650 Ti.
- The embedder sits behind a tiny interface (`embed_images`, `embed_text`,
  `space`, `dim`). Unit tests use a deterministic fake so the test suite
  doesn't need a 600 MB model or a GPU; one `@pytest.mark.slow` test uses
  the real model to prove "a photo of a dog" ranks the dog above the beach.

---

## 7. Search

```
"dogs at the beach"
      │
      ▼  CLIP text encoder → L2-normalise
 text vector (512-d)
      │
      ▼  FAISS IndexFlatIP.search(k=100)   (inner product on unit vectors = cosine)
 [(embedding_id, score), …]
      │
      ▼  SQLite: embeddings ⋈ files ⋈ image_metadata
 hydrated results (id, filename, thumbnail url, captured_at, gps, score)
      │
      ▼  optional filters: root, date range, has_gps
 ranked list → renderer grid
```

**Why exact (flat) search, not an approximate index?** At 100k vectors a
brute-force cosine over 512-d takes single-digit milliseconds and uses
~200 MB. Approximate indexes (IVF, HNSW) trade recall for speed and need
training/tuning; that trade only becomes worth it around a million
vectors. We measure first.

**Why filenames are not the primary search?** Because the whole point is
"IMG_4821.jpg" being findable as "sunset over the sea". Filename/folder
text will later become *one more signal* (a text space fused in), never
the main one.

**Filtering.** Filters that are cheap and selective (date range, root) are
applied as a FAISS `IDSelector` built from a SQL query when the filter
removes most candidates, otherwise post-filtered on an over-fetched top-K.
Same result either way; we pick whichever is faster once we can measure.

**Free bonus: image → image.** "More like this" is the same index queried
with an image vector instead of a text vector. Costs nothing to add later.

### 7.1 Becoming multimodal and compound (later)

Each modality lands in whichever space suits it (visual → CLIP space,
text-like → a small local text embedding model, e.g. `bge-small`). A query
fans out to every space, and the per-space ranked lists are merged with
**Reciprocal Rank Fusion** (score = Σ 1/(k + rank)), which is robust to the
spaces having incomparable raw scores. Entity matches (a person's name, a
place) enter the fusion the same way, as another ranked list. "Rahul at
the beach in Goa during 2024" becomes: *person filter* (faces) ∩ *place
filter* (geocode) ∩ *time filter* + *scene ranking* (CLIP). The user never
writes that; a small query-understanding step derives it.

---

## 8. The knowledge graph (later — but designed for now)

Not a graph database. Two tables:

```
entities          id, type ('person'|'place'|'event'|'topic'), name, canonical_key
file_entities     file_id, entity_id, source, confidence, evidence   (file ↔ entity edges)
entity_relations  src_entity_id, dst_entity_id, type, weight          (entity ↔ entity edges)
```

Entities arrive in this order, each a separate milestone:

1. **Places** — from GPS via an *offline* reverse geocoder (no network). A
   photo taken at 15.49°N 73.82°E becomes `taken_at → Goa`.
2. **Events** — photos clustered by time *and* place ("~40 photos within
   3 days and 30 km" → an event, auto-named "Goa · March 2024", renameable).
3. **Topics** — from CLIP: cluster the embeddings, label clusters by their
   nearest text prompts ("beach", "moon", "dog").
4. **People** — face detection + face embeddings (local), clustered; the
   user names a cluster "Rahul". People are **never auto-named** and faces
   are strictly opt-in.

SQLite answers 1–2 hop questions ("everything connected to Goa Trip") in
milliseconds at this scale. We would only consider a graph engine if
multi-hop traversal became a hot path — it will not for a personal archive.

*As built (M7)* — `app/graph.py`, run by the worker after every embedding
pass; all of it is derived data that can be rebuilt from the index.

- **Tables:** `entities (type, key, name, data)`, `file_entities (file,
  entity, score, source)`, `entity_relations (source, target, kind,
  weight)`, plus `enrichment (file, pipeline, version)` so each step only
  handles photos it hasn't seen (bumping a version reprocesses everything;
  a changed photo is reprocessed automatically).
- **Places:** GeoNames `cities5000` (every place with 5,000+ people, CC BY
  4.0, ~6 MB downloaded once into `models/geonames/`) in a 3-D FAISS index on
  the unit sphere. Naming rule, tuned on real coordinates: skip city
  sections (`PPLX`, e.g. Dharavi), and among places within 25 km pick the
  highest population / (1 + km/5)², so Panaji's suburbs say "Panjim" (not
  the village next door, and not a bigger town 12 km away). Each photo is
  linked to its city, region and country, chained by `part_of`.
- **Scenes** (the "topics" above): CLIP zero-shot over ~85 everyday labels,
  computed from the stored vectors (no photo is re-read). A label needs
  both ≥ 0.2 probability and ≥ 0.26 similarity: on real photos correct
  labels scored 0.29–0.31 and wrong guesses ≤ 0.25. Limitation: one strong
  subject can mask another ("Dog" scores 0.31 on a dog on a beach, "Beach"
  only 0.20), so scenes describe the main subject; full-sentence search
  still finds "dogs at the beach".
- **Events:** sessions split on an 8-hour gap or a 100 km jump; consecutive
  sessions in the same region, away from home, within 48 h and 21 days
  merge into a trip ("Goa trip, March 2024"). *Home* is the region with
  the most distinct days of photos, not the most photos (a two-day trip
  can out-shoot two weeks at home). Events need 5+ photos with a capture
  date (screenshots have none).
- **Relations:** `part_of` (places), `took_place_in` (event → place), and
  `appears_with` between things that share 2+ photos, weighted by count.
- **Search:** a query naming a known place ("beach in Goa") is limited to
  that place and ranked by the rest of the words; "photos from Goa" is
  everything there, newest first.
- **People:** not built. It needs a face model and must be opt-in.

---

## 9. How the visual views consume the data

Every view is a **read-only projection over SQLite**; none of them touch
the indexing pipeline.

| View | Backend endpoint | Data source | Client rendering |
|---|---|---|---|
| Search / Grid | `POST /search` | FAISS + files + image_metadata | virtualised thumbnail grid |
| Map | `GET /map/points` (bbox, zoom; server clusters when >5k points) | image_metadata lat/lon | **MapLibre GL** + OpenStreetMap tiles — open source, no API key, no paid service; tiles can be cached for offline later |
| Timeline | `GET /timeline?granularity=` | captured_at (fallback mtime), later events | histogram + event lanes |
| Clusters | `GET /clusters` | `clusters` table produced by an offline job | card grid with representative thumbnails |
| Graph | `GET /graph?entity=&depth=` | entities + relations | force-directed layout (d3-force) with thumbnail nodes |

Because the shape is "one DB, many projections", adding the timeline
means adding one endpoint and one page — no changes to extraction,
embedding, or storage.

*As built (M5–M6):*
- **Grid**: justified rows that keep each photo's shape; search shows the
  results close to the best hit first (CLIP scores are only comparable
  within one query) with the rest behind "Show more"; the library view
  pages 120 at a time instead of virtualising.
- **Map**: `GET /map/points` returns every located photo as GeoJSON and
  MapLibre clusters it client-side (supercluster, in MapLibre's worker).
  Simpler than server-side clustering and fast at the sizes we have; the
  bbox/zoom endpoint is the upgrade path once a library has 50k+ located
  photos. MapLibre's worker can't be found by the bundler, so
  `apps/web/scripts/copy-maplibre-worker.mjs` serves it from
  `public/maplibre/` (run automatically before `dev` and `build`).

---

## 10. Repository structure

```
cortex/
  apps/
    desktop/electron/        main.ts, preload.ts   (the only trusted JS)
    web/src/
      app/                   Next.js routes (search, map, …)
      components/ui/         shadcn/ui primitives
      features/              search/, indexing/, map/  (feature-scoped components + hooks)
      lib/api.ts             typed backend client
    backend/app/
      main.py                app factory, router registration
      core/                  config.py, db.py (connection, migrations)
      api/                   routers: health, roots, index, search, images, (map…)
      indexing/              scanner.py, hashing.py, pipeline.py, jobs.py,
                             extractors/image.py  (one file per modality later)
      ai/                    embedder.py (interface + fake), clip.py (real)
      vectors/               faiss_store.py
      search/                service.py
    backend/tests/
  storage/                   database/, vectors/, thumbnails/   (never committed)
  models/                    downloaded weights                   (never committed)
  scripts/                   e2e smoke script, maintenance
  docs/
```

Rule of thumb: a module is added when the second consumer appears, not
before. `scanner.py` exists because M1 needed it; `extractors/` appears in
M2 because EXIF and thumbnails are two consumers of "open this image".

---

## 11. Biggest technical risks (ranked)

1. **Shipping Python inside an Electron app.** PyTorch alone is hundreds of
   MB; a PyInstaller bundle is 1–3 GB and antivirus software dislikes it.
   *Mitigation:* the backend is already a standalone process with an HTTP
   contract, so packaging is isolated to one milestone (M7) and never
   blocks feature work. We do not touch it until search is worth shipping.
2. **Memory on an 8 GB machine.** Electron + Next dev server + PyTorch is a
   real squeeze. *Mitigation:* §4.3 — lazy load, `draft()` decoding,
   small batches, thumbnails; measure RSS during indexing in M3.
3. **Expectation gap in search quality.** CLIP does not know who "Rahul"
   is or where "Goa" is. The MVP answers *scene/object* queries ("moon",
   "dogs on a beach"); people and places require the entity layer (§8).
   *Mitigation:* say so in the UI's example prompts; don't fake it.
4. **First-run cost.** ~600 MB model download + `torch` install.
   *Mitigation:* honest `models/status` UI; CPU wheel first; cache in
   `models/`.
5. **OneDrive Files On-Demand placeholders** (§4.5). *Mitigation:* detect
   the attribute, flag, skip.
6. **FAISS/SQLite drift.** *Mitigation:* §3.1/§4.4 — SQLite is truth,
   FAISS is rebuilt on mismatch.
7. **Hostile inputs**: corrupt files, 500-megapixel images, malformed EXIF.
   *Mitigation:* per-file isolation, Pillow bomb protection, tests with
   synthetic bad files.
8. **Security regression pressure** as features grow (the temptation to
   pass paths to the renderer, or disable `webSecurity` to show images).
   *Mitigation:* §5 — id-based image serving makes the secure path the
   easy path.
9. **Faces** (later): accuracy on small groups, and the ethics of it.
   Local-only, opt-in, user-named clusters only.

---

## 12. What we build first, and what we explicitly do not

**First (the MVP, in this order):** persistent incremental index with
EXIF and thumbnails → CLIP + FAISS + `/search` → the search UI → map. See
`roadmap.md`.

**Explicitly not yet:** documents, audio, video, faces/people, places,
events, clusters, timeline, graph, RAG/chat, LLM agents, file watcher,
packaging/installer, multi-user, cloud anything. Each has a defined slot in
the roadmap; none is started before the photo search is *reliable*, not
merely working.

---

## 13. Testing strategy

| Layer | How | Notes |
|---|---|---|
| Scanner, hashing, change detection | pytest, temp dirs | includes "file deleted mid-scan", permission errors, cloud placeholder |
| EXIF / metadata | pytest with synthetic images built by Pillow + `piexif` | GPS, dates, orientation, *missing* EXIF, malformed EXIF |
| DB + migrations | pytest on a temp SQLite file | schema up, idempotent migrate |
| Pipeline resumability | pytest: run, kill after N files (simulated), rerun, assert no re-work | the crash-recovery guarantee, tested not assumed |
| FAISS store | pytest with random vectors | add / remove / search / rebuild-from-SQLite |
| Search ranking | pytest with a **fake embedder** | deterministic; asserts filter + ordering logic |
| Real model | one `@pytest.mark.slow` test | dog photo outranks beach photo for "a dog" |
| API | `httpx` test client against a temp DB | every endpoint's happy path + validation errors |
| Frontend | `tsc`, ESLint; unit tests only for non-trivial logic | the UI is verified in the real app |
| End to end | `scripts/e2e_smoke.mjs` drives the running Electron app over CDP | the same technique used to verify M1 |

The fake embedder is the important idea: it lets 95% of the test suite run
in seconds with no model, no GPU, and no download, while one slow test
keeps us honest about the real thing.
