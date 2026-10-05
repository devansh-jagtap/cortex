# Cortex

Cortex is a 100% local, AI-powered desktop application for searching your own files
by meaning, not filename — "dogs at the beach" instead of `IMG_4821.jpg`.

- **Local-first.** No cloud AI APIs, no uploading your files anywhere. All AI
  processing happens on your machine.
- **Non-destructive.** Cortex never renames, moves, deletes, or modifies your
  files. It builds a separate index that points at your existing folders.
- **Incremental.** Re-scanning only processes new or changed files.

This repository is being built incrementally, one verified milestone at a time.

- [`docs/architecture.md`](docs/architecture.md) — the system design, with the
  reasoning behind every major decision
- [`docs/roadmap.md`](docs/roadmap.md) — the build order, milestone by milestone
- [`docs/development.md`](docs/development.md) — how to run and test it locally

## Current status

| Milestone | State |
| --- | --- |
| M1 · Electron + Next.js + FastAPI wired together | done |
| M2 · Persistent incremental index (SQLite, EXIF, thumbnails, background jobs) | done |
| M3 · Filesystem watcher | done |
| M4 · Semantic search core (OpenCLIP + FAISS) | done |
| M5 · Search UI | done |
| M6 · Map view | done |
| M7 · Knowledge graph foundation (places, scenes, events) | done |
| M8 · Galaxy view of the graph | done |
| M9 · Robustness and performance | done |
| M10 · Packaging (installer) | next |

What works today: add a folder and Cortex catalogs every photo in it in the
background — dimensions, EXIF date/GPS/camera, and a 320px thumbnail — with
a live progress bar, Cancel, and Resume. Re-scanning only touches new or
changed files; moved files keep their record; deleted files are marked
missing. Corrupt images are recorded and skipped without stopping the job.
Indexed folders are watched: new, edited, moved, and deleted photos are
picked up automatically within a couple of seconds, with no Rescan needed.
Every photo is embedded locally with OpenCLIP, and searching by meaning
("a privacy settings menu") takes about 150 ms. The app is search-first and
keyboard-driven: type a description, arrow into the results, open a photo
to see where it lives and when it was taken, and open the original in your
default viewer.
The Map tab shows every photo that recorded a location, clustered by place.
Cortex also works out where each photo was taken, what's in it, and which
event or trip it belongs to, and links them: click "Goa" to see its photos
and everything connected to it. Searches like "photos from Goa" just work.
The Galaxy tab draws all of it as constellations: places, events, and
scenes as stars, related ones joined, unrelated groups floating apart.

See [`docs/roadmap-revised.md`](docs/roadmap-revised.md) for the full plan.

## Stack

Electron · Next.js (React, TypeScript, Tailwind CSS) · Python · FastAPI ·
OpenCLIP · FAISS · SQLite · watchdog

## Quick start

See [`docs/development.md`](docs/development.md) for full setup instructions.

```bash
# 1. Backend (Python 3.13, one-time setup)
cd apps/backend
py -3.13 -m venv venv
./venv/Scripts/pip install -r requirements.txt

# 2. From the repo root, run backend + web dev server together
npm install
npm run dev

# 3. In a separate terminal, launch the desktop shell
cd apps/desktop
npm install
npm run start
```

## Credits

- Place names: [GeoNames](https://www.geonames.org), licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Map tiles: © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.
- Image model: [OpenCLIP](https://github.com/mlfoundations/open_clip) ViT-B/32 trained on LAION-2B.
