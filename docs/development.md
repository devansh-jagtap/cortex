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

# Fast read-only preview: counts files, writes nothing
curl -X POST http://127.0.0.1:8000/scan -H "Content-Type: application/json"   -d "{\"path\": \"C:/Users/you/Pictures\"}"

# Index a folder in the background, then poll progress
curl -X POST http://127.0.0.1:8000/index/start -H "Content-Type: application/json"   -d "{\"path\": \"C:/Users/you/Pictures\"}"
curl http://127.0.0.1:8000/index/status
curl -X POST http://127.0.0.1:8000/index/cancel

# What's in the library
curl http://127.0.0.1:8000/library
curl "http://127.0.0.1:8000/images?limit=20&offset=0"
curl http://127.0.0.1:8000/images/1/metadata
curl -o thumb.jpg http://127.0.0.1:8000/images/1/thumbnail
```

Scans run one at a time; starting a second folder queues it, and starting
the same folder twice returns 409.
Re-indexing the same folder is incremental: unchanged files are skipped
without being read, so it is close to instant.

## AI search (OpenCLIP + FAISS)

Every indexed photo is also turned into a 512-number vector by OpenCLIP
(ViT-B/32, `laion2b_s34b_b79k`), and so is your search text; the photos
whose vectors point the most in the same direction are the results.

- **First run downloads the model** (~580 MB) into `models/`. That happens
  automatically the first time there is a photo to embed; `GET
  /models/status` reports `downloading` / `loading` / `ready`.
- Embedding runs in the background after every scan or watcher update, on
  the CPU, using half the cores so the computer stays usable: about 10
  photos/second on the development laptop. It uses the 320px thumbnails
  (CLIP only looks at 224px), so originals are not decoded twice.
- Vectors are stored in SQLite (`embeddings` table, the source of truth);
  the FAISS index lives in memory and is rebuilt/reconciled from SQLite.

```bash
curl -X POST http://127.0.0.1:8000/search -H "Content-Type: application/json"   -d "{\"query\": \"dogs at the beach\", \"limit\": 20}"
curl http://127.0.0.1:8000/models/status
```

**GPU (optional).** The default install is the CPU build of PyTorch. With
an NVIDIA GPU, the CUDA build is roughly 10x faster: use the pip command
from the "Get Started" selector on pytorch.org (Windows, pip, your CUDA
version), keeping `torch==2.14.1` / `torchvision==0.29.1`, and run it with
`./venv/Scripts/python.exe -m pip`. Cortex picks the GPU automatically when
PyTorch can see one.

**Windows Smart App Control.** If you see `DLL load failed ... An
Application Control policy has blocked this file`, Windows is refusing an
unsigned native library that is too new to have a reputation. `faiss-cpu`
is pinned to 1.12.0 for this reason; don't upgrade it without checking that
`python -c "import faiss"` still works.

## Automatic updates (the watcher)

Every folder Cortex has indexed is also *watched* using the operating
system's change notifications (via `watchdog`), so nothing is polled:

- a new or edited photo is indexed about a second after it is written;
- a deleted photo (or a deleted folder) is marked missing;
- a renamed or moved photo keeps its record (it is matched by content hash).

Changes are batched after ~1 second of quiet, so a large copy produces one
update rather than hundreds. On startup Cortex also re-scans every folder
once to catch anything that changed while it was closed; this is
incremental, so unchanged files are not even read.

All writes to the index (scans and watcher updates) go through a single
background worker, one task at a time (`app/jobs.py`).

### What Cortex skips

So that "Index this computer" (your home folder) doesn't drown in icons and
caches, Cortex never looks inside: hidden folders (`.git`, `.cache`, ...),
`AppData`, `node_modules`, `venv`, `__pycache__`, `site-packages`, the
recycle bin, system folders at a drive root (`C:\Windows`,
`C:\Program Files`, ...), and its own `storage/` and `models/` folders.
These rules apply only *below* a folder you chose, never to the folder
itself (see `app/exclusions.py`).

## Running the tests

```bash
cd apps/backend
./venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./venv/Scripts/python.exe -m pytest -q
```

Tests never touch the real `storage/` folder: `tests/conftest.py` points
`CORTEX_STORAGE_DIR` at a fresh temporary directory for every test, and the
test images are generated on the fly (`tests/images.py`), including real
EXIF dates and GPS tags. Search tests use a tiny fake embedder
(`tests/fakes.py`) that places images by colour, so ranking is tested
without the 580 MB model. One test uses the real model; run it with:

```bash
CORTEX_SLOW_TESTS=1 ./venv/Scripts/python.exe -m pytest -q -k real_clip
```

## Storage locations

Cortex never writes to your photo folders. Everything it creates lives
under `storage/` in the repo (override with the `CORTEX_STORAGE_DIR`
environment variable):

- `storage/database/cortex.db` — the SQLite index (source of truth). Uses
  WAL mode, so you may also see `cortex.db-wal` / `cortex.db-shm` next to it.
- `storage/thumbnails/<aa>/<hash>.jpg` — 320px previews, named by the
  BLAKE2b hash of the original's content, so identical photos share one
  thumbnail.
- `storage/vectors/` — the FAISS index file(s) (from the search milestone).
- `models/` — local model weights (OpenCLIP), downloaded once and cached.

None of these are committed to git (see `.gitignore`). Deleting `storage/`
is safe: it only throws away the index, which is rebuilt on the next scan.

## Known limitations

- OneDrive "online-only" placeholder files are skipped (reading them would
  silently download them). Make a folder "Always keep on this device" if you
  want Cortex to index it.
- The Electron `start` script always compiles TypeScript before launching;
  there's no hot-reload for the main process (the Next.js renderer does
  hot-reload via its own dev server).
- Packaging (a distributable installer) is not set up yet.

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
