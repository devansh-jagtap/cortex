import os
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import app.graph as graph
import app.main as main
from app.database import get_connection
from app.embedder import get_embedder
from app.geonames import ReverseGeocoder
from app.graph import GraphBuilder
from app.indexer import index_folder
from app.jobs import IndexJobManager
from app.search import SearchService
from tests.images import make_image

client = TestClient(main.app)

GOA = (15.4989, 73.8278)
MUMBAI = (19.0728, 72.8826)
PARIS = (48.8534, 2.3488)
RED, GREEN, BLUE = (220, 20, 20), (20, 200, 20), (20, 20, 220)


def _city(gid, name, lat, lon, cc, admin1, population=100000, code="PPL"):
    return "\t".join(
        [str(gid), name, name, "", str(lat), str(lon), "P", code, cc, "", admin1, "", "", "", str(population), "", "", "", ""]
    )


@pytest.fixture
def geocoder(tmp_path):
    folder = tmp_path / "geonames"
    folder.mkdir()
    (folder / "cities5000.txt").write_text(
        "\n".join(
            [
                _city(1260607, "Panaji", *GOA, "IN", "33", population=70991),
                _city(1255131, "Taleigao", 15.4667, 73.8333, "IN", "33", population=17148),
                _city(1262331, "Mormugao", 15.38914, 73.81491, "IN", "33", population=102345),
                _city(1275339, "Mumbai", *MUMBAI, "IN", "16", population=12691836),
                _city(1272866, "Dharavi", 19.0400, 72.8540, "IN", "16", population=700000, code="PPLX"),
                _city(2988507, "Paris", *PARIS, "FR", "11"),
            ]
        ),
        encoding="utf-8",
    )
    (folder / "admin1CodesASCII.txt").write_text(
        "IN.33\tGoa\tGoa\t1\nIN.16\tMaharashtra\tMaharashtra\t2\nFR.11\tÎle-de-France\tIle-de-France\t3\n",
        encoding="utf-8",
    )
    (folder / "countryInfo.txt").write_text(
        "#ISO\tISO3\tISO-Numeric\tfips\tCountry\n"
        "IN\tIND\t356\tIN\tIndia\tNew Delhi\nFR\tFRA\t250\tFR\tFrance\tParis\n",
        encoding="utf-8",
    )
    return ReverseGeocoder(data_dir=folder, allow_download=False)


@pytest.fixture
def colour_scenes(monkeypatch):
    monkeypatch.setattr(graph, "SCENES", [("Red", "red"), ("Green", "green"), ("Blue", "blue")])


@pytest.fixture
def services(monkeypatch, geocoder):
    search = SearchService(get_embedder)
    builder = GraphBuilder(get_embedder, geocoder)
    monkeypatch.setattr(main, "search_service", search)
    monkeypatch.setattr(main, "jobs", IndexJobManager(search, builder))
    return search, builder


def _taken(when: datetime) -> str:
    return when.strftime("%Y:%m:%d %H:%M:%S")


def _entities(type_):
    conn = get_connection()
    try:
        return {
            r["name"]: r["photos"]
            for r in conn.execute(
                "SELECT e.name, COUNT(fe.file_id) AS photos FROM entities e "
                "LEFT JOIN file_entities fe ON fe.entity_id = e.id WHERE e.type = ? GROUP BY e.id",
                (type_,),
            )
        }
    finally:
        conn.close()


def _build(tmp_path, builder, search=None):
    index_folder(str(tmp_path))
    search = search or SearchService(get_embedder)
    search.embed_pending()
    search.sync()
    return builder.run()


def test_reverse_geocoding_finds_city_region_and_country(geocoder):
    near_goa, offshore, open_ocean = geocoder.lookup([(15.55, 73.75), (15.5, 72.6), (0.0, -30.0)])

    assert (near_goa.city_name, near_goa.region_name, near_goa.country_name) == ("Panaji", "Goa", "India")
    assert offshore.city_name is None and offshore.region_name == "Goa"  # ~130 km out: region only
    assert open_ocean is None


def test_the_biggest_nearby_place_is_named_not_a_village_or_neighbourhood(geocoder):
    in_panaji, next_to_village, in_neighbourhood = geocoder.lookup(
        [GOA, (15.4670, 73.8330), (19.0401, 72.8541)]
    )

    assert in_panaji.city_name == "Panaji"  # Mormugao is bigger, but 12 km away
    assert next_to_village.city_name == "Panaji"  # Taleigao is closer, Panaji is the city
    assert in_neighbourhood.city_name == "Mumbai"  # sections of a city are never named


def test_places_become_linked_entities_with_hierarchy(tmp_path, geocoder):
    make_image(tmp_path / "a.jpg", gps=GOA)
    make_image(tmp_path / "b.jpg", gps=(15.52, 73.80), color=BLUE)
    make_image(tmp_path / "no_gps.jpg", color=GREEN)

    stats = _build(tmp_path, GraphBuilder(get_embedder, geocoder))

    assert stats.places_linked == 2
    assert _entities("place") == {"Panaji": 2, "Goa": 2, "India": 2}
    conn = get_connection()
    kinds = {
        (a, b)
        for a, b in conn.execute(
            "SELECT s.name, t.name FROM entity_relations r JOIN entities s ON s.id = r.source_id "
            "JOIN entities t ON t.id = r.target_id WHERE r.kind = 'part_of'"
        )
    }
    conn.close()
    assert kinds == {("Panaji", "Goa"), ("Goa", "India")}


def test_scenes_are_labelled_from_stored_vectors(tmp_path, geocoder, colour_scenes, fake_embedder):
    make_image(tmp_path / "red.jpg", color=RED)
    make_image(tmp_path / "blue.jpg", color=BLUE)
    builder = GraphBuilder(get_embedder, geocoder)
    _build(tmp_path, builder)
    images_seen = fake_embedder.images_embedded

    builder.run()  # nothing new: no photo is relabelled or re-read

    assert _entities("scene") == {"Red": 1, "Blue": 1}
    assert fake_embedder.images_embedded == images_seen


def test_a_session_of_photos_becomes_an_event(tmp_path, geocoder):
    start = datetime(2024, 3, 14, 10, 0)
    for i in range(6):
        make_image(tmp_path / f"s{i}.jpg", gps=MUMBAI, taken=_taken(start.replace(minute=i * 5)), color=(i * 30, 9, 9))
    for i in range(2):  # too few for an event of their own, days later
        make_image(tmp_path / f"x{i}.jpg", gps=MUMBAI, taken=_taken(datetime(2024, 4, 2, 9, i)), color=(9, i * 60, 9))

    stats = _build(tmp_path, GraphBuilder(get_embedder, geocoder))

    assert stats.events == 1
    assert _entities("event") == {"Mumbai, Mar 14, 2024": 6}


def test_days_away_from_home_merge_into_one_trip(tmp_path, geocoder):
    n = 0
    for day in range(1, 11):  # home: a photo or two most days in Mumbai
        make_image(tmp_path / f"home{day}.jpg", gps=MUMBAI, taken=_taken(datetime(2024, 3, day, 18, 0)), color=(day * 20, 1, 1))
    for day in (20, 21):  # two days of photos in Goa, a night apart
        for i in range(6):
            n += 1
            make_image(
                tmp_path / f"goa{n}.jpg", gps=GOA, taken=_taken(datetime(2024, 3, day, 9 + i, 0)), color=(1, n * 15, 1)
            )

    _build(tmp_path, GraphBuilder(get_embedder, geocoder))

    assert _entities("event") == {"Goa trip, March 2024": 12}


def test_things_sharing_photos_are_related(tmp_path, geocoder, colour_scenes):
    for i in range(3):
        make_image(tmp_path / f"{i}.jpg", gps=PARIS, color=RED)

    stats = _build(tmp_path, GraphBuilder(get_embedder, geocoder))

    conn = get_connection()
    pairs = {
        frozenset((a, b)): w
        for a, b, w in conn.execute(
            "SELECT s.name, t.name, r.weight FROM entity_relations r JOIN entities s ON s.id = r.source_id "
            "JOIN entities t ON t.id = r.target_id WHERE r.kind = 'appears_with'"
        )
    }
    conn.close()
    assert pairs[frozenset(("Paris", "Red"))] == 3
    assert stats.relations >= 3


def test_changed_photo_is_relabelled(tmp_path, geocoder, colour_scenes):
    path = make_image(tmp_path / "a.jpg", color=RED)
    builder = GraphBuilder(get_embedder, geocoder)
    search = SearchService(get_embedder)
    _build(tmp_path, builder, search)
    assert _entities("scene") == {"Red": 1}

    make_image(path, color=BLUE)
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    _build(tmp_path, builder, search)

    assert _entities("scene") == {"Blue": 1}


def _index_via_api(path):
    assert client.post("/index/start", json={"path": str(path)}).status_code == 202
    assert main.jobs.wait_idle(timeout=60)


def test_api_exposes_entities_on_photos_and_entity_pages(tmp_path, services, colour_scenes):
    for i in range(2):
        make_image(tmp_path / f"goa{i}.jpg", gps=GOA, color=RED)
    _index_via_api(tmp_path)

    image_id = client.get("/images").json()["items"][0]["id"]
    entities = client.get(f"/images/{image_id}/metadata").json()["entities"]
    names = [(e["type"], e["name"]) for e in entities]
    assert ("place", "Goa") in names and ("scene", "Red") in names

    places = client.get("/entities?type=place").json()["items"]
    goa = next(e for e in places if e["name"] == "Goa")
    assert goa["photos"] == 2 and goa["level"] == "region"

    detail = client.get(f"/entities/{goa['id']}").json()
    assert detail["photos_total"] == 2
    assert {r["name"] for r in detail["related"]} >= {"India", "Panaji", "Red"}


def test_search_understands_place_names(tmp_path, services, colour_scenes):
    make_image(tmp_path / "goa_red.jpg", gps=GOA, color=RED)
    make_image(tmp_path / "goa_blue.jpg", gps=(15.50, 73.83), color=BLUE)
    make_image(tmp_path / "paris_red.jpg", gps=PARIS, color=RED)
    _index_via_api(tmp_path)

    everything_in_goa = client.post("/search", json={"query": "photos from Goa"}).json()
    assert everything_in_goa["place"]["name"] == "Goa"
    assert {r["filename"] for r in everything_in_goa["results"]} == {"goa_red.jpg", "goa_blue.jpg"}

    blue_in_goa = client.post("/search", json={"query": "blue in goa"}).json()
    assert blue_in_goa["refined_by"] == "blue"
    assert blue_in_goa["results"][0]["filename"] == "goa_blue.jpg"


def test_removed_folder_leaves_no_places_behind(tmp_path, services, colour_scenes):
    goa, paris = tmp_path / "goa", tmp_path / "paris"
    goa.mkdir()
    paris.mkdir()
    make_image(goa / "a.jpg", gps=GOA, color=RED)
    make_image(paris / "b.jpg", gps=PARIS, color=BLUE)
    _index_via_api(goa)
    _index_via_api(paris)
    paris_root = next(r["id"] for r in client.get("/library").json()["roots"] if r["path"] == str(paris))

    client.delete(f"/roots/{paris_root}")
    assert main.jobs.wait_idle(timeout=60)

    names = {e["name"] for e in client.get("/entities").json()["items"]}
    assert "Goa" in names
    assert not names & {"Paris", "France", "Île-de-France", "Blue"}
    assert _entities("place").keys() == {"Panaji", "Goa", "India"}


def test_photo_network_links_look_alikes_into_separate_named_groups(tmp_path, services, colour_scenes):
    for i in range(3):
        make_image(tmp_path / f"red{i}.jpg", color=(200 + i * 10, 20 + i * 5, 20))
        make_image(tmp_path / f"blue{i}.jpg", color=(20, 20 + i * 5, 200 + i * 10))
    _index_via_api(tmp_path)

    body = client.get("/graph/photos").json()

    assert body["total"] == 6 and len(body["nodes"]) == 6
    group_of = {n["filename"]: n["cluster"] for n in body["nodes"]}
    assert len({group_of[f"red{i}.jpg"] for i in range(3)}) == 1
    assert len({group_of[f"blue{i}.jpg"] for i in range(3)}) == 1
    assert group_of["red0.jpg"] != group_of["blue0.jpg"]
    labels = {c["label"] for c in body["clusters"]}
    assert labels == {"Red", "Blue"}
    name_of = {n["id"]: n["filename"] for n in body["nodes"]}
    for edge in body["edges"]:  # no link ever crosses between the groups
        assert name_of[edge["source"]][:3] == name_of[edge["target"]][:3]
        assert edge["similarity"] >= 0.55


def test_photo_network_focus_brings_the_closest_look_alikes(tmp_path, services, colour_scenes):
    for i in range(3):
        make_image(tmp_path / f"red{i}.jpg", color=(200 + i * 10, 20, 20))
    make_image(tmp_path / "blue.jpg", color=(20, 20, 220))
    _index_via_api(tmp_path)
    red0 = next(n["id"] for n in client.get("/graph/photos").json()["nodes"] if n["filename"] == "red0.jpg")

    body = client.get(f"/graph/photos?focus={red0}").json()

    names = [n["filename"] for n in body["nodes"]]
    assert names[0] == "red0.jpg"
    assert set(names[:3]) == {"red0.jpg", "red1.jpg", "red2.jpg"}
    assert client.get("/graph/photos?focus=999999").status_code == 404


def test_photo_network_is_empty_before_anything_is_analyzed(services):
    assert client.get("/graph/photos").json() == {"nodes": [], "edges": [], "clusters": [], "total": 0}
