"""The knowledge graph: places, scenes, and events, and how they connect.

Everything here is derived from the index (files, image_metadata,
embeddings) and can be thrown away and rebuilt at any time:

- places: GPS -> city, region, country (offline, see geonames.py);
- scenes: CLIP zero-shot labels ("beach", "food", "code") from the image
  vectors we already store, so no photo is read again;
- events: photos grouped by time and place; several days away from home
  become one trip;
- relations: city part_of region part_of country, event took_place_in
  place, and "appears_with" between things that share photos.

The `enrichment` table records which photos each step has handled, so a
rerun only does the new ones.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import numpy as np

from app.database import get_connection
from app.embedder import Embedder
from app.geonames import ReverseGeocoder

log = logging.getLogger("cortex.graph")

PLACES_VERSION = 3
SCENES_VERSION = 2

# (name, CLIP prompt). Plain everyday concepts for personal photos and
# screenshots; names are what the user sees.
SCENES: list[tuple[str, str]] = [
    ("Beach", "a photo of a beach"),
    ("Sea", "a photo of the sea"),
    ("Mountains", "a photo of mountains"),
    ("Forest", "a photo of a forest"),
    ("Snow", "a photo of snow"),
    ("Desert", "a photo of a desert"),
    ("Lake", "a photo of a lake"),
    ("River", "a photo of a river"),
    ("Waterfall", "a photo of a waterfall"),
    ("Sunset", "a photo of a sunset"),
    ("Night sky", "a photo of the night sky"),
    ("Moon", "a photo of the moon"),
    ("City skyline", "a photo of a city skyline"),
    ("Street", "a photo of a city street"),
    ("Architecture", "a photo of a building"),
    ("Temple", "a photo of a temple"),
    ("Church", "a photo of a church"),
    ("Bridge", "a photo of a bridge"),
    ("Park", "a photo of a park"),
    ("Flowers", "a photo of flowers"),
    ("Plants", "a photo of plants"),
    ("Countryside", "a photo of fields in the countryside"),
    ("Car", "a photo of a car"),
    ("Motorcycle", "a photo of a motorcycle"),
    ("Bicycle", "a photo of a bicycle"),
    ("Train", "a photo of a train"),
    ("Airplane", "a photo of an airplane"),
    ("Boat", "a photo of a boat"),
    ("Dog", "a photo of a dog"),
    ("Cat", "a photo of a cat"),
    ("Bird", "a photo of a bird"),
    ("Horse", "a photo of a horse"),
    ("Cow", "a photo of a cow"),
    ("Food", "a photo of food"),
    ("Dessert", "a photo of a dessert"),
    ("Coffee", "a photo of a cup of coffee"),
    ("Drinks", "a photo of drinks"),
    ("Restaurant", "a photo of a restaurant"),
    ("Party", "a photo of a party"),
    ("Wedding", "a photo of a wedding"),
    ("Concert", "a photo of a concert"),
    ("Festival", "a photo of a festival"),
    ("Sports", "a photo of people playing sports"),
    ("Cricket", "a photo of a cricket match"),
    ("Football", "a photo of a football match"),
    ("Gym", "a photo of a gym"),
    ("Selfie", "a selfie"),
    ("Group photo", "a group photo of friends"),
    ("Baby", "a photo of a baby"),
    ("Portrait", "a portrait photo of a person"),
    ("Document", "a photo of a document"),
    ("Receipt", "a photo of a receipt"),
    ("Whiteboard", "a photo of a whiteboard"),
    ("Handwritten notes", "a photo of handwritten notes"),
    ("Book", "a photo of a book"),
    ("Screenshot", "a screenshot"),
    ("Code", "a screenshot of source code"),
    ("Chat", "a screenshot of a chat conversation"),
    ("Website", "a screenshot of a website"),
    ("Settings", "a screenshot of a settings menu"),
    ("Spreadsheet", "a screenshot of a spreadsheet"),
    ("Chart", "a chart or graph"),
    ("Map", "a map"),
    ("Slide", "a presentation slide"),
    ("Meme", "a meme"),
    ("Drawing", "a drawing"),
    ("Painting", "a painting"),
    ("Computer", "a photo of a computer"),
    ("Phone", "a photo of a smartphone"),
    ("Kitchen", "a photo of a kitchen"),
    ("Bedroom", "a photo of a bedroom"),
    ("Living room", "a photo of a living room"),
    ("Office", "a photo of an office"),
    ("Classroom", "a photo of a classroom"),
    ("Shopping", "a photo of a shop"),
    ("Clothes", "a photo of clothes"),
    ("Shoes", "a photo of shoes"),
    ("Rain", "a photo of rain"),
    ("Clouds", "a photo of clouds in the sky"),
    ("Fireworks", "a photo of fireworks"),
    ("Birthday", "a photo of a birthday cake"),
    ("Graduation", "a photo of a graduation ceremony"),
    ("Museum", "a photo inside a museum"),
    ("Swimming pool", "a photo of a swimming pool"),
    ("Hiking", "a photo of people hiking"),
    ("Camping", "a photo of a tent while camping"),
]
SCENE_TEMPERATURE = 100.0  # CLIP's own logit scale
SCENE_MIN_PROBABILITY = 0.2
# Every label must also clear an absolute similarity: below it, the "best"
# label is just the least-bad guess. Measured with ViT-B/32 on real photos
# and screenshots: correct labels scored 0.29-0.31, wrong guesses <= 0.25.
SCENE_MIN_SIMILARITY = 0.26
SCENES_PER_PHOTO = 3

SESSION_GAP_HOURS = 8  # a longer pause starts a new session
TRIP_GAP_HOURS = 48  # sessions in the same region this close together form one trip
TRIP_MAX_DAYS = 21
MOVE_KM = 100  # jumping this far also starts a new session
MIN_EVENT_PHOTOS = 5


@dataclass
class GraphStats:
    places_linked: int = 0
    scenes_linked: int = 0
    events: int = 0
    relations: int = 0
    errors: list[str] = field(default_factory=list)


class GraphBuilder:
    def __init__(self, embedder_factory: Callable[[], Embedder], geocoder: ReverseGeocoder | None = None) -> None:
        self._embedder_factory = embedder_factory
        self._geocoder = geocoder or ReverseGeocoder()
        self._label_cache: dict[tuple, np.ndarray] = {}
        self._lock = threading.Lock()

    def run(self, should_cancel: Callable[[], bool] | None = None) -> GraphStats:
        """Bring the graph up to date. One failing step doesn't stop the others."""
        stats = GraphStats()
        with self._lock:
            for step in (self._places, self._scenes, self._events, self._relations):
                if should_cancel and should_cancel():
                    break
                try:
                    step(stats)
                except Exception as exc:
                    log.exception("graph step %s failed", step.__name__)
                    stats.errors.append(f"{step.__name__.strip('_')}: {type(exc).__name__}: {exc}")
        return stats

    # ---------------------------------------------------------------- places

    def _places(self, stats: GraphStats) -> None:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT f.id, m.latitude, m.longitude FROM files f
                JOIN image_metadata m ON m.file_id = f.id
                WHERE f.status = 'indexed' AND m.latitude IS NOT NULL AND m.longitude IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM enrichment e WHERE e.file_id = f.id
                                  AND e.pipeline = 'places' AND e.version = ?)
                """,
                (PLACES_VERSION,),
            ).fetchall()
            for start in range(0, len(rows), 1000):
                batch = rows[start : start + 1000]
                places = self._geocoder.lookup([(r["latitude"], r["longitude"]) for r in batch])
                for row, place in zip(batch, places):
                    conn.execute("DELETE FROM file_entities WHERE file_id = ? AND source = 'places'", (row["id"],))
                    if place is not None:
                        for entity_id, score in _place_entities(conn, place):
                            _link(conn, row["id"], entity_id, score, "places")
                        stats.places_linked += 1
                    _mark(conn, row["id"], "places", PLACES_VERSION)
                conn.commit()
            if rows:
                conn.execute(
                    "DELETE FROM entities WHERE type = 'place' AND id NOT IN (SELECT entity_id FROM file_entities)"
                )
                conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------------- scenes

    def _label_vectors(self, embedder: Embedder) -> np.ndarray:
        key = (embedder.name, tuple(prompt for _, prompt in SCENES))
        if key not in self._label_cache:
            self._label_cache[key] = np.stack([embedder.embed_text(prompt) for _, prompt in SCENES])
        return self._label_cache[key]

    def _scenes(self, stats: GraphStats) -> None:
        embedder = self._embedder_factory()
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT e.file_id, e.vector FROM embeddings e JOIN files f ON f.id = e.file_id
                WHERE f.status = 'indexed' AND e.model = ?
                  AND NOT EXISTS (SELECT 1 FROM enrichment x WHERE x.file_id = e.file_id
                                  AND x.pipeline = 'scenes' AND x.version = ?)
                """,
                (embedder.name, SCENES_VERSION),
            ).fetchall()
            if not rows:
                return
            labels = self._label_vectors(embedder)
            scene_ids = [
                _upsert_entity(conn, "scene", f"scene:{name.lower()}", name, {}) for name, _ in SCENES
            ]
            for start in range(0, len(rows), 512):
                batch = rows[start : start + 512]
                vectors = np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in batch])
                similarity = vectors @ labels.T
                logits = SCENE_TEMPERATURE * similarity
                probs = np.exp(logits - logits.max(axis=1, keepdims=True))
                probs /= probs.sum(axis=1, keepdims=True)
                for row, sim, prob in zip(batch, similarity, probs):
                    conn.execute("DELETE FROM file_entities WHERE file_id = ? AND source = 'scenes'", (row["file_id"],))
                    for label in np.argsort(-prob)[:SCENES_PER_PHOTO]:
                        if prob[label] >= SCENE_MIN_PROBABILITY and sim[label] >= SCENE_MIN_SIMILARITY:
                            _link(conn, row["file_id"], scene_ids[label], float(prob[label]), "scenes")
                            stats.scenes_linked += 1
                    _mark(conn, row["file_id"], "scenes", SCENES_VERSION)
                conn.commit()
            conn.execute(
                "DELETE FROM entities WHERE type = 'scene' AND id NOT IN (SELECT entity_id FROM file_entities)"
            )
            conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------------- events

    def _events(self, stats: GraphStats) -> None:
        """Recomputed from scratch each time: sorting by date is cheap."""
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT f.id, m.captured_at, m.latitude, m.longitude,
                       (SELECT fe.entity_id FROM file_entities fe JOIN entities en ON en.id = fe.entity_id
                        WHERE fe.file_id = f.id AND en.type = 'place' AND en.key LIKE 'region:%') AS region_id,
                       (SELECT fe.entity_id FROM file_entities fe JOIN entities en ON en.id = fe.entity_id
                        WHERE fe.file_id = f.id AND en.type = 'place' AND en.key LIKE 'city:%') AS city_id
                FROM files f JOIN image_metadata m ON m.file_id = f.id
                WHERE f.status = 'indexed' AND m.captured_at IS NOT NULL
                ORDER BY m.captured_at, f.id
                """
            ).fetchall()
            events = _group_events(rows)
            names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM entities WHERE type = 'place'")}

            keep = set()
            conn.execute("DELETE FROM file_entities WHERE source = 'events'")
            conn.execute("DELETE FROM entity_relations WHERE kind = 'took_place_in'")
            for event in events:
                key = f"event:{event.photos[0]['id']}"
                keep.add(key)
                name = _event_name(event, names)
                start, end = event.photos[0]["captured_at"], event.photos[-1]["captured_at"]
                entity_id = _upsert_entity(
                    conn, "event", key, name, {"start": start, "end": end, "trip": event.trip}
                )
                for photo in event.photos:
                    _link(conn, photo["id"], entity_id, 1.0, "events")
                for place_id in {p for p in (event.region_id, event.city_id) if p}:
                    _relate(conn, entity_id, place_id, "took_place_in", len(event.photos))
            stale = [
                r["id"] for r in conn.execute("SELECT id, key FROM entities WHERE type = 'event'") if r["key"] not in keep
            ]
            conn.executemany("DELETE FROM entities WHERE id = ?", [(i,) for i in stale])
            conn.commit()
            stats.events = len(events)
        finally:
            conn.close()

    # ------------------------------------------------------------- relations

    def _relations(self, stats: GraphStats) -> None:
        """Things that share photos are related, weighted by how many they share."""
        conn = get_connection()
        try:
            conn.execute("DELETE FROM entity_relations WHERE kind = 'appears_with'")
            conn.execute(
                """
                INSERT INTO entity_relations (source_id, target_id, kind, weight)
                SELECT a.entity_id, b.entity_id, 'appears_with', COUNT(*)
                FROM file_entities a
                JOIN file_entities b ON b.file_id = a.file_id AND b.entity_id > a.entity_id
                JOIN files f ON f.id = a.file_id AND f.status = 'indexed'
                JOIN entities ea ON ea.id = a.entity_id
                JOIN entities eb ON eb.id = b.entity_id
                -- places relate to each other via part_of, and events to places via
                -- took_place_in; co-occurrence there would only repeat that.
                WHERE NOT (ea.type IN ('place', 'event') AND eb.type IN ('place', 'event'))
                GROUP BY a.entity_id, b.entity_id
                HAVING COUNT(*) >= 2
                """
            )
            stats.relations = conn.execute("SELECT COUNT(*) FROM entity_relations").fetchone()[0]
            conn.commit()
        finally:
            conn.close()


# ------------------------------------------------------------------- events


@dataclass
class _Event:
    photos: list
    trip: bool = False
    region_id: int | None = None
    city_id: int | None = None


def _km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a["latitude"], a["longitude"], b["latitude"], b["longitude"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(min(1.0, h)))


def _dominant(photos, field_name):
    counts = Counter(p[field_name] for p in photos if p[field_name])
    return counts.most_common(1)[0][0] if counts else None


def _group_events(rows) -> list[_Event]:
    # 1. Sessions: split on a long pause or a big jump in location.
    sessions: list[list] = []
    last_located = None
    for row in rows:
        new = not sessions or row["captured_at"] - sessions[-1][-1]["captured_at"] > SESSION_GAP_HOURS * 3600
        if not new and row["latitude"] is not None and last_located is not None and _km(last_located, row) > MOVE_KM:
            new = True
        if new:
            sessions.append([])
        sessions[-1].append(row)
        if row["latitude"] is not None:
            last_located = row

    # 2. Trips: consecutive sessions in the same region away from home merge.
    # Home is where photos are taken on the most *days*, not the most photos:
    # a two-day trip can easily out-shoot two weeks at home.
    home_days = Counter(
        region for region, _ in {(r["region_id"], int(r["captured_at"] // 86400)) for r in rows if r["region_id"]}
    )
    home = home_days.most_common(1)[0][0] if home_days else None
    events: list[_Event] = []
    for session in sessions:
        region = _dominant(session, "region_id")
        prev = events[-1] if events else None
        if (
            prev is not None
            and region is not None
            and region != home
            and prev.region_id == region
            and session[0]["captured_at"] - prev.photos[-1]["captured_at"] <= TRIP_GAP_HOURS * 3600
            and session[-1]["captured_at"] - prev.photos[0]["captured_at"] <= TRIP_MAX_DAYS * 86400
        ):
            prev.photos.extend(session)
            prev.trip = True
            prev.city_id = _dominant(prev.photos, "city_id")
            continue
        events.append(_Event(photos=list(session), region_id=region, city_id=_dominant(session, "city_id")))
    return [e for e in events if len(e.photos) >= MIN_EVENT_PHOTOS]


def _event_name(event: _Event, names: dict[int, str]) -> str:
    start = datetime.fromtimestamp(event.photos[0]["captured_at"])
    if event.trip and event.region_id:
        return f"{names.get(event.region_id, 'Trip')} trip, {start:%B %Y}"
    place = names.get(event.city_id) or names.get(event.region_id)
    day = f"{start:%b} {start.day}, {start.year}"
    return f"{place}, {day}" if place else day


# -------------------------------------------------------------------- store


def _upsert_entity(conn, type_: str, key: str, name: str, data: dict) -> int:
    conn.execute(
        "INSERT INTO entities (type, key, name, data) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(type, key) DO UPDATE SET name = excluded.name, data = excluded.data",
        (type_, key, name, json.dumps(data)),
    )
    return conn.execute("SELECT id FROM entities WHERE type = ? AND key = ?", (type_, key)).fetchone()[0]


def _link(conn, file_id: int, entity_id: int, score: float, source: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO file_entities (file_id, entity_id, score, source) VALUES (?, ?, ?, ?)",
        (file_id, entity_id, score, source),
    )


def _relate(conn, source_id: int, target_id: int, kind: str, weight: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO entity_relations (source_id, target_id, kind, weight) VALUES (?, ?, ?, ?)",
        (source_id, target_id, kind, weight),
    )


def _mark(conn, file_id: int, pipeline: str, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO enrichment (file_id, pipeline, version) VALUES (?, ?, ?)",
        (file_id, pipeline, version),
    )


def _place_entities(conn, place) -> list[tuple[int, float]]:
    """City, region, and country entities for a geocoded point, linked part_of."""
    country = _upsert_entity(
        conn, "place", f"country:{place.country_code}", place.country_name, {"level": "country"}
    )
    linked = [(country, 1.0)]
    parent = country
    if place.region_key:
        region = _upsert_entity(
            conn, "place", f"region:{place.region_key}", place.region_name,
            {"level": "region", "country": place.country_name},
        )
        _relate(conn, region, country, "part_of", 1)
        linked.append((region, 1.0))
        parent = region
    if place.city_id:
        city = _upsert_entity(
            conn, "place", f"city:{place.city_id}", place.city_name,
            {"level": "city", "region": place.region_name, "country": place.country_name},
        )
        _relate(conn, city, parent, "part_of", 1)
        linked.append((city, max(0.0, 1 - place.distance_km / 75)))
    return linked
