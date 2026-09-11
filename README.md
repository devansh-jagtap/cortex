# Cortex

Cortex is a 100% local, AI-powered desktop application for searching your own files
by meaning, not filename — "dogs at the beach" instead of `IMG_4821.jpg`.

- **Local-first.** No cloud AI APIs, no uploading your files anywhere. All AI
  processing happens on your machine.
- **Non-destructive.** Cortex never renames, moves, deletes, or modifies your
  files. It builds a separate index that points at your existing folders.
- **Incremental.** Re-scanning only processes new or changed files.

This repository is being built incrementally, one verified milestone at a time.
See [`docs/architecture.md`](docs/architecture.md) for the system design and
[`docs/development.md`](docs/development.md) for how to run it locally.

## Current status

**Milestone 1 (foundation) — complete.**

Electron, Next.js, and FastAPI are wired together end to end:

1. Electron opens a desktop window and loads the Next.js UI.
2. The user clicks **Select Folder**, which opens a native OS folder picker
   (via a secure Electron IPC bridge — no raw filesystem access is exposed to
   the renderer).
3. The selected path is sent to the FastAPI backend, which recursively scans
   it and classifies files as supported images vs. everything else.
4. The counts are displayed in the UI.

Not yet implemented: AI embeddings, vector search, SQLite metadata storage,
EXIF extraction, natural-language search, the image grid/results UI, and the
map view. See `docs/architecture.md` for the planned roadmap.

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
