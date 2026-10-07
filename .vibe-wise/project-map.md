# Project Map

## Purpose
Cortex: a local desktop app that helps users manage and find their images by
meaning ("dogs at the beach"), with fast search, folder watching, and a
"galaxy" network of similar photos.

## Requirements
- Install and use on your own computer; costs nothing to run.
- Local-first: user files and the index stay on the user's machine; no
  developer-run storage holding user data.
- Fast, easy retrieval and management of images and folders.
- New (not designed yet): log in from another device to reach the files and
  folders of a chosen synced device, from anywhere, with authentication.
- New (not designed yet): add member accounts with view and manage
  permissions, "like Nest" (a household sharing model).

## Components (implemented, verified in code)
- Desktop shell: Electron (`apps/desktop/electron/main.ts`, `preload.ts`):
  window, folder picker, opens originals, starts/stops the engine, per-launch token.
- UI: Next.js static export (`apps/web/src`): Search, Galaxy, viewer, library panel.
- Engine: Python FastAPI (`apps/backend/app`): indexer + watcher (`indexer.py`,
  `watcher.py`, `jobs.py`), CLIP embeddings + FAISS search (`embedder.py`,
  `search.py`), places/scenes/events (`graph.py`, `geonames.py`), API (`main.py`).
- Storage: SQLite + thumbnails in `storage/`, model weights in `models/`.

## Main Flow
User folder --(watchdog events / scans)--> Engine indexer --> SQLite + thumbnails
Engine --(CLIP vectors)--> FAISS index (rebuilt from SQLite)
UI --(HTTP + launch token, 127.0.0.1 only)--> Engine --> results/thumbnails
Another device --?--> this computer   (not designed)

## Data and Trust Boundaries
- Engine listens on 127.0.0.1 only; in app mode every request needs a
  per-launch token injected by Electron.
- No accounts, no remote access, no server run by the developer.
- Network use: one-time model and GeoNames downloads only (map tiles removed).

## Build and Deployment
- `npm run app` (repo root): builds UI, launches Electron, which runs the engine.
- Dev: `npm run dev` + `apps/desktop: npm run start`.
- Tests: `apps/backend: ./venv/Scripts/python.exe -m pytest -q`.
- No installer yet (unsigned exes blocked by Windows Smart App Control).

## Unknowns
- How another device reaches this computer (direct, relay, tunnel, ?).
- Where accounts and logins live, and who runs that part.
- What "member" can see and do; how permissions are enforced.
- What happens when the home computer is off.
