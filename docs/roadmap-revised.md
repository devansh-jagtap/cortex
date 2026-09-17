# Cortex: Revised Roadmap

## The Product Vision (Corrected)

**Cortex is a personal AI file system for your computer.**

You install it once. It indexes your entire computer in the background. It watches for new/changed files automatically. Everything stays local — no cloud APIs, no uploading. You search semantically ("sunset photos"), and it finds them instantly. The knowledge graph visualizes your data as connected clusters — images linked to people, places, scenes, events.

The end result is not a "folder search tool", but a **visual, explorable knowledge space of everything on your computer**.

---

## Revised Milestone Order

### Phase 1: Foundation & Core Search

#### ✅ M1 · Communication Foundation (Done)
Electron + Next.js + FastAPI wired end-to-end. User selects a folder, backend scans it, results appear.
- **Proves:** The three processes communicate; security model holds.
- **Do not rewrite.** Continue from here.

#### 🔨 M2 · Persistent Incremental Index
- SQLite schema: `roots`, `files`, `image_metadata`, `index_jobs`
- Change detection: size+mtime → BLAKE2b hash → new / unchanged / modified / moved / missing
- EXIF extraction: date taken, GPS, camera model, dimensions, orientation
- Thumbnail generation (320px preview images)
- Background worker thread for indexing
- Resumable after crash via `index_jobs` table
- **Outcome:** Pick a folder, Cortex catalogs it. Next scan: only new/changed photos. Database remembers everything.

#### ⏳ M3 · Filesystem Watcher
- `watchdog` library monitors `roots` for new/deleted/modified files
- Real-time detection (not polling)
- Trigger small background jobs: new files go through the M2 pipeline
- Graceful handling: deleted files are marked as missing; moved files are relinked by hash
- **Outcome:** Add a photo to your Pictures folder. Cortex notices and indexes it within 5 seconds. No "Rescan" button needed.

#### ⏳ M4 · Semantic Search Core (Backend Only)
- OpenCLIP ViT-B/32 embedder (lazy load, device auto-select)
- `embeddings` table in SQLite (vectors stored as BLOBs)
- FAISS `IndexFlatIP` with `IDMap2` for explicit ids
- Rebuild FAISS from SQLite on startup mismatch (crash recovery)
- `POST /search` endpoint: text query → CLIP embedding → FAISS → results
- **Outcome:** `curl /search '{"query":"sunset"}'` returns photo ids ranked by similarity. Search quality proven before UI.

#### ⏳ M5 · Search UI (The MVP Product)
- Large search box with example prompts
- Real-time results as a virtualized thumbnail grid
- Click a photo → detail view with metadata (date, location, camera, size)
- "Open Original" button (by file id, secure)
- Empty / loading / error states
- Show indexing status: "Watching… last updated 2 min ago" or "Indexing 45 of 1,200"
- Dark theme first
- **Outcome:** User types "dogs at the beach", sees photos instantly.

#### ⏳ M6 · Map View
- `GET /map/points` endpoint (server-side clustering for performance)
- MapLibre GL + OpenStreetMap tiles (free, cacheable)
- Every photo with GPS shows as a pin
- Click a location → see all photos from that area
- Navigation: Search tab · Map tab
- **Outcome:** See all your travels on a map.

#### ⏳ M7 · Knowledge Graph Foundation
**Not yet a visualization.** Build the data model and populate it:

- Entity types: `person`, `place`, `scene`, `event`
- `entities` table: id, type, name, canonical_key
- `file_entities` table: which files contain which entities
- `entity_relations` table: relationships between entities
- Automatic extraction pipelines (one per entity type):
  - **Places:** offline reverse geocoding from GPS → `place` entities
  - **Scenes/Topics:** CLIP embedding clustering + zero-shot labels → `scene` entities (e.g., "beach", "moon", "dog")
  - **Events:** temporal + spatial clustering → `event` entities (e.g., "Goa Trip 2024")
  - **People:** optional local face detection + clustering → `person` entities (user-named, opt-in)
- Relationships: `image ↔ place`, `image ↔ scene`, `image ↔ event`, `image ↔ person`, `person ↔ place`, `event ↔ place`
- **Outcome:** SQLite now knows "this photo was taken in Goa, contains a beach, shows Rahul, is part of the Goa Trip event". Not yet visualized, but the data exists.

#### ⏳ M8 · Knowledge Graph Visualization ("Galaxy View")
- New tab: "Graph"
- Force-directed layout (d3-force or similar)
- Nodes: entities (people, places, events, scenes) with thumbnail previews
- Edges: relationships
- Click a node → highlights connected nodes and photos
- Progressive rendering (don't try to render 1M nodes at once; cluster and simplify for view)
- Zoom/pan controls
- "Galaxy" metaphor: disconnected groups appear as separate clusters; related entities form constellations
- **Outcome:** Visual exploration of your data. Start with "Rahul" → see him connected to "Goa", "Goa Trip", beach photos, other people from that trip.

#### ⏳ M9 · Robustness & Performance
- Incremental FAISS rebuilds (don't recompute all vectors on every change)
- Batch embedding (process 100 new files at once instead of one-by-one)
- Benchmark on 50k+ photos; tune batch size, worker count, FAISS search parameters
- Graceful degradation: one corrupt photo doesn't stop indexing
- Cloud placeholder detection (OneDrive Files On-Demand, Google Drive)

#### ⏳ M10 · Packaging
- Next.js static export (`output: 'export'`)
- Electron spawns and supervises the Python backend
- PyInstaller bundle for backend
- Electron-builder installer
- Per-launch auth token between Electron and Python
- **Outcome:** Users can install Cortex like any other desktop app.

---

## Phase 2: Refinement & Expansion (Not Yet)

After the graph is working and search is reliable:

- **Timeline view** — see photos organized by date
- **Similar images** — "show me more like this"
- **People improvements** — better face detection, privacy controls
- **Documents** — PDF / DOCX text extraction and search
- **Audio / Video** — Whisper transcription, keyframe extraction
- **Cross-file relationships** — explicit linking (e.g., "this receipt is from the Goa trip")
- **RAG / Agents** — only after retrieval is rock solid

---

## What Changes from Original Roadmap

| Aspect | Original | Revised |
|---|---|---|
| **Focus** | "Photo search app" | "Personal AI file system" |
| **Folder selection** | Primary UX | Dev/testing only |
| **File watcher** | M14+ (late) | M3 (early) |
| **Knowledge graph** | M8–M14 (late) | M7–M8 (core) |
| **Graph visualization** | "Just a graph" | "Galaxy" with clusters and progressive rendering |
| **Whole-computer indexing** | Not mentioned | Foundation assumption |
| **Scope** | Images → documents → video → audio | **Images only until M10+** |

---

## Key Architectural Decisions (Unchanged)

✅ **SQLite is truth; FAISS is cache** — rebuild FAISS from vectors stored in SQLite
✅ **Local only** — no cloud APIs, no uploading
✅ **Electron + Next.js + Python** — don't rewrite
✅ **Background workers** — indexing, embedding, file watching all async
✅ **Secure by default** — images served by id, paths never reach the renderer

---

## The Vision in One Picture

```
                        CORTEX

    ┌─────────────────────────────────┐
    │  Your Computer (whole system)   │
    │  auto-indexed in background     │
    └────────────┬────────────────────┘
                 │
        ┌────────┴────────┐
        │                 │
    ┌───▼────┐      ┌─────▼────┐
    │ Search │      │   Graph  │
    │  (M5)  │      │   (M8)   │
    └────────┘      └──────────┘
        │                │
        └────────┬───────┘
                 │
            ┌────▼─────┐
            │  SQLite  │
            │  (truth) │
            └──────────┘
                 │
        ┌────────┴────────┐
        │                 │
    ┌───▼──────┐    ┌─────▼──────┐
    │  FAISS   │    │  Entities  │
    │  (cache) │    │ & Relations│
    └──────────┘    └────────────┘
```

User types a query → Search UI → FAISS → ranked results → click one → detail view or explore in Graph.

---

## Agreed? Changes Needed?

Before I code M2, confirm:
- ✅ Whole-computer indexing (not folder-picker UX)
- ✅ File watcher in M3 (not later)
- ✅ Knowledge graph as M7–M8 core features (not M14+)
- ✅ "Galaxy" visualization (not just a generic force-directed graph)
- ✅ Images only in Phase 1 (documents/video/audio in Phase 2)
- ✅ SQLite + FAISS architecture (no changes)

**Or do you want to adjust any of these milestones?**
