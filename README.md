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
| M8 · Galaxy: a network of look-alike photos | done |
| M9 · Robustness and performance | done |
| M10 · Packaging | one-command app done; installer waits on code signing |

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
The Galaxy tab draws your photos as a network: every photo is a star,
joined to the photos that look like it, so similar photos gather into
groups (named after what's in them) and unrelated ones float apart.

See [`docs/roadmap-revised.md`](docs/roadmap-revised.md) for the full plan.

## Stack

Electron · Next.js (React, TypeScript, Tailwind CSS) · Python · FastAPI ·
OpenCLIP · FAISS · SQLite · watchdog

## Quick start

One-time setup (Python 3.13 and Node.js 20+; details in
[`docs/development.md`](docs/development.md)):

```bash
cd apps/backend && py -3.13 -m venv venv && ./venv/Scripts/pip install -r requirements.txt && cd ../..
npm install --prefix apps/web
npm install --prefix apps/desktop
```

Then start Cortex with one command, from the project folder:

```bash
npm run app
```

This builds the interface and opens the Cortex window, which starts its
own engine in the background and stops it when you close the window. The
first launch downloads the AI model (~580 MB) once. For hot-reloading
development instead, see [`docs/development.md`](docs/development.md).

## Credits

- Place names: [GeoNames](https://www.geonames.org), licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Map tiles: © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.
- Image model: [OpenCLIP](https://github.com/mlfoundations/open_clip) ViT-B/32 trained on LAION-2B.
