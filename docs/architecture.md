# Cortex Architecture

## Overview

Cortex is a desktop application split into three processes:

```
                 CORTEX DESKTOP
                       |
                       v
                   ELECTRON
                       |
                       v
                NEXT.JS / REACT
                       |
                 HTTP (localhost)
                       |
                       v
                   FASTAPI
                       |
         +-------------+-------------+
         |             |             |
         v             v             v
      INDEXER       SEARCH        METADATA
         |             |             |
         v             v             v
      OpenCLIP       FAISS        SQLite
         |
         v
   Local filesystem
```

- **Electron** owns the application window, desktop lifecycle, and native OS
  integration (folder picker, opening files). It is the only layer with
  filesystem/process access, and it exposes that access to the renderer
  through a narrow, explicit preload bridge — never wholesale.
- **Next.js / React** renders the UI: search, image grid, previews, indexing
  progress, map. It talks to the backend over plain HTTP on localhost.
- **FastAPI (Python)** owns everything AI/data related: filesystem scanning,
  metadata/EXIF extraction, embeddings, FAISS vector search, and the SQLite
  metadata store. It runs as a local-only service bound to `127.0.0.1`.

Each layer only does its own job. The UI never touches the filesystem
directly; the backend never renders anything; Electron never runs AI code.

## Repository layout

```
cortex/
  apps/
    desktop/          Electron shell
      electron/
        main.ts        Main process: window, IPC handlers
        preload.ts      Context-isolated bridge exposed to the renderer
    web/               Next.js frontend (TypeScript, Tailwind)
    backend/           Python/FastAPI backend
      app/
        main.py         FastAPI app, routes
        scanner.py      Recursive folder scanning
      venv/             Python virtual environment (not committed)
  storage/
    database/          SQLite database file(s) (not committed)
    vectors/           FAISS index file(s) (not committed)
  models/              Local model weights (not committed)
  scripts/             One-off dev/maintenance scripts
  docs/                This documentation
```

## Process communication

- **Renderer -> Electron main**: `contextBridge` + `ipcRenderer.invoke`, via
  `window.cortex.*` (defined in `apps/desktop/electron/preload.ts`). Currently
  exposes only `selectFolder()`. This is the only channel between the UI and
  the OS/filesystem — `contextIsolation: true` and `nodeIntegration: false`
  are set on the `BrowserWindow`, and nothing else is exposed.
- **Renderer -> Backend**: plain `fetch()` calls to `http://127.0.0.1:8000`.
  CORS on the backend is restricted to the known dev origins
  (`localhost:3000`). Since the backend only ever binds to loopback and only
  the trusted Electron app talks to it, this is safe for a local desktop app.

## Data model (planned)

### SQLite `images` table

| column         | notes                                   |
| -------------- | ---------------------------------------- |
| id             | primary key                              |
| path           | absolute path to the original file       |
| filename       | basename, for display                    |
| extension      | lowercase, e.g. `.jpg`                   |
| size           | bytes                                    |
| hash           | content hash, used for incremental index |
| created_at     | filesystem creation time                 |
| modified_at    | filesystem modification time             |
| width / height | pixel dimensions                         |
| latitude / longitude | from EXIF GPS, nullable            |
| captured_at    | from EXIF, nullable                      |
| embedding_id   | maps to a FAISS vector ID                |
| indexed_at     | when Cortex last processed this file     |

Cortex never stores image binaries in SQLite — only references and metadata.

### FAISS vector index

Every indexed image gets one CLIP embedding, added to a FAISS index. FAISS
vector IDs are explicit and stored alongside the SQLite row (`embedding_id`),
never relied upon as an implicit array position — vectors will be added,
updated, and removed over the life of an index.

## Roadmap

Built now (Milestone 1):

- Electron shell with secure preload bridge
- Next.js UI shell
- FastAPI backend with `/health` and `/scan`
- Recursive image discovery with graceful per-file error handling

Next milestones, in order:

1. SQLite metadata store + incremental indexing (hash/mtime comparison)
2. EXIF extraction
3. OpenCLIP embeddings + FAISS index, background indexing worker
4. Indexing progress polling endpoint + UI progress bar
5. Natural-language search endpoint + results grid + image preview
6. Secure local image serving (thumbnails + originals) to the renderer
7. Map view for GPS-tagged photos

Deferred beyond the photo-search MVP: documents, audio, video, face
recognition, knowledge graph, RAG/agent chat. See the product brief for the
full long-term vision — none of it is implemented yet.
