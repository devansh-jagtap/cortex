# Development Guide

## Prerequisites

- Node.js 20+ and npm
- Python 3.13 (used deliberately instead of the newest 3.14 — the ML
  libraries planned for later milestones, e.g. PyTorch/FAISS, tend to lag
  behind the newest CPython release). On Windows, install via the
  [Python launcher](https://docs.python.org/3/using/windows.html#launcher)
  so `py -3.13` resolves.
- Git

## One-time setup

```bash
# Backend virtual environment
cd apps/backend
py -3.13 -m venv venv
./venv/Scripts/pip install -r requirements.txt
cd ../..

# Root dev tooling (concurrently, for running backend + web together)
npm install

# Web app
cd apps/web
npm install
cd ../..

# Desktop shell
cd apps/desktop
npm install
cd ../..
```

## Running Cortex in development

Two terminals:

**Terminal 1** — backend + web dev server, from the repo root:

```bash
npm run dev
```

This runs FastAPI on `http://127.0.0.1:8000` and the Next.js dev server on
`http://localhost:3000`.

**Terminal 2** — the desktop shell, once the above is up:

```bash
cd apps/desktop
npm run start
```

This compiles `electron/*.ts` to `dist/` and launches Electron, which loads
the running Next.js dev server and talks to the FastAPI backend directly.

You can also run each process individually:

```bash
npm run dev:backend   # FastAPI only, from repo root
npm run dev:web       # Next.js dev server only, from repo root
```

## Verifying the backend independently

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/scan \
  -H "Content-Type: application/json" \
  -d "{\"path\": \"C:/Users/you/Pictures\"}"
```

## Storage locations

Nothing is written outside the repo yet — Milestone 1 only scans and reports
counts. Once indexing lands:

- `storage/database/` — the SQLite metadata database
- `storage/vectors/` — the FAISS index file(s)
- `models/` — local model weights (OpenCLIP), downloaded once and cached

None of these are committed to git (see `.gitignore`).

## Known limitations (Milestone 1)

- No AI/search yet — `/scan` only counts files, it does not index them.
- No SQLite or FAISS integration yet.
- The Electron `start` script always compiles TypeScript before launching;
  there's no file-watcher/hot-reload for the main process yet (the Next.js
  renderer does hot-reload via its own dev server).
- Packaging (a distributable installer) is not set up — `output: 'export'`
  for the Next.js static build and an Electron packager (e.g. electron-builder)
  are future work once there's a real UI to ship.

## Commands reference

| Command                          | Where          | What it does                          |
| --------------------------------- | -------------- | -------------------------------------- |
| `npm run dev`                     | repo root      | backend + Next.js dev server           |
| `npm run dev:backend`             | repo root      | FastAPI only                           |
| `npm run dev:web`                 | repo root      | Next.js dev server only                |
| `npm run start`                   | `apps/desktop` | build + launch the Electron shell      |
| `npm run build`                   | `apps/desktop` | compile `electron/*.ts` to `dist/`     |
| `npm run build`                   | `apps/web`     | production Next.js build (type-checks) |
| `npm run lint`                    | `apps/web`     | ESLint                                 |
