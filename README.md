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
| M3 · Filesystem watcher | next |
| M4 · Semantic search core (OpenCLIP + FAISS) | planned |
| M5 · Search UI | planned |
| M6 · Map view | planned |

What works today: add a folder and Cortex catalogs every photo in it in the
background — dimensions, EXIF date/GPS/camera, and a 320px thumbnail — with
a live progress bar, Cancel, and Resume. Re-scanning only touches new or
changed files; moved files keep their record; deleted files are marked
missing. Corrupt images are recorded and skipped without stopping the job.

See [`docs/roadmap-revised.md`](docs/roadmap-revised.md) for the full plan.

## Stack

Electron · Next.js (React, TypeScript, Tailwind CSS) · Python · FastAPI ·
OpenCLIP (planned) · FAISS (planned) · SQLite (planned)

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
