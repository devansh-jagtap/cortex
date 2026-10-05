"""Offline reverse geocoding: GPS coordinates -> city, region, country.

Uses GeoNames data (CC BY 4.0, https://www.geonames.org), downloaded once
into models/geonames/ (~5 MB). Lookups never leave the machine: the nearest
city is found with a tiny FAISS inner-product index over points on the unit
sphere, where the largest dot product is the smallest great-circle distance.
"""

from __future__ import annotations

import io
import logging
import math
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np

from app.storage import models_dir

log = logging.getLogger("cortex.geonames")

DOWNLOAD_BASE = "https://download.geonames.org/export/dump/"
CITIES_FILE = "cities5000.txt"  # every place with 5,000+ people
REQUIRED = (CITIES_FILE, "admin1CodesASCII.txt", "countryInfo.txt")

EARTH_RADIUS_KM = 6371.0
# Name the place people would say, not simply the nearest one: a photo in
# Panaji's suburbs should say "Panjim", not the village next door, but a
# bigger town 12 km away shouldn't win either. Among places within 25 km,
# the one with the highest population / (1 + km / 5)^2 is named.
PREFER_LARGER_WITHIN_KM = 25.0
SIZE_DISTANCE_KM = 5.0
CANDIDATES = 32
CITY_WITHIN_KM = 75.0  # farther than this, only the region and country are claimed
REGION_WITHIN_KM = 300.0  # farther than this (open sea, wilderness), nothing is claimed


@dataclass(frozen=True)
class Place:
    distance_km: float
    city_id: int | None
    city_name: str | None
    region_key: str | None
    region_name: str | None
    country_code: str
    country_name: str


def geonames_dir() -> Path:
    return models_dir() / "geonames"


def _unit_vectors(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    lat, lon = np.radians(lats), np.radians(lons)
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=1).astype(np.float32)


class ReverseGeocoder:
    def __init__(self, data_dir: Path | None = None, allow_download: bool = True) -> None:
        self._dir = data_dir
        self._allow_download = allow_download
        self._lock = threading.Lock()
        self._index: faiss.IndexFlatIP | None = None

    def lookup(self, coords: list[tuple[float, float]]) -> list[Place | None]:
        if not coords:
            return []
        self._ensure_loaded()
        lats = np.array([c[0] for c in coords], dtype=np.float64)
        lons = np.array([c[1] for c in coords], dtype=np.float64)
        dots, idx = self._index.search(_unit_vectors(lats, lons), CANDIDATES)
        places: list[Place | None] = []
        for row_dots, row_idx in zip(dots, idx):
            candidates = [
                (math.acos(max(-1.0, min(1.0, float(d)))) * EARTH_RADIUS_KM, int(i))
                for d, i in zip(row_dots, row_idx)
                if i >= 0
            ]
            if not candidates or candidates[0][0] > REGION_WITHIN_KM:
                places.append(None)
                continue
            nearby = [c for c in candidates if c[0] <= PREFER_LARGER_WITHIN_KM]
            distance, i = (
                max(nearby, key=lambda c: self._population[c[1]] / (1 + c[0] / SIZE_DISTANCE_KM) ** 2)
                if nearby
                else candidates[0]
            )
            city_id, name, country, admin1 = self._cities[i]
            region_key = f"{country}.{admin1}" if admin1 else None
            near = distance <= CITY_WITHIN_KM
            places.append(
                Place(
                    distance_km=round(distance, 1),
                    city_id=city_id if near else None,
                    city_name=name if near else None,
                    region_key=region_key if region_key in self._regions else None,
                    region_name=self._regions.get(region_key) if region_key else None,
                    country_code=country,
                    country_name=self._countries.get(country, country),
                )
            )
        return places

    def _ensure_loaded(self) -> None:
        if self._index is not None:
            return
        with self._lock:
            if self._index is not None:
                return
            folder = self._dir or geonames_dir()
            self._ensure_files(folder)
            cities, lats, lons, population = [], [], [], []
            with open(folder / CITIES_FILE, encoding="utf-8") as f:
                for line in f:
                    p = line.rstrip("\n").split("\t")
                    # PPLX = a section of a city (e.g. a neighbourhood): name the city instead.
                    if len(p) < 15 or p[7] == "PPLX":
                        continue
                    cities.append((int(p[0]), p[1], p[8], p[10]))
                    lats.append(float(p[4]))
                    lons.append(float(p[5]))
                    population.append(int(p[14] or 0))
            self._regions = {}
            with open(folder / "admin1CodesASCII.txt", encoding="utf-8") as f:
                for line in f:
                    p = line.rstrip("\n").split("\t")
                    if len(p) >= 2:
                        self._regions[p[0]] = p[1]
            self._countries = {}
            with open(folder / "countryInfo.txt", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    p = line.rstrip("\n").split("\t")
                    if len(p) >= 5:
                        self._countries[p[0]] = p[4]
            index = faiss.IndexFlatIP(3)
            index.add(_unit_vectors(np.array(lats), np.array(lons)))
            self._cities = cities
            self._population = population
            self._index = index
            log.info("loaded %d places from GeoNames", len(cities))

    def _ensure_files(self, folder: Path) -> None:
        missing = [name for name in REQUIRED if not (folder / name).exists()]
        if not missing:
            return
        if not self._allow_download:
            raise FileNotFoundError(f"GeoNames data missing in {folder}: {', '.join(missing)}")
        folder.mkdir(parents=True, exist_ok=True)
        for name in missing:
            remote = name.replace(".txt", ".zip") if name == CITIES_FILE else name
            log.info("downloading %s from GeoNames", remote)
            request = urllib.request.Request(DOWNLOAD_BASE + remote, headers={"User-Agent": "Cortex (local photo index)"})
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
            if remote.endswith(".zip"):
                payload = zipfile.ZipFile(io.BytesIO(payload)).read(name)
            tmp = folder / (name + ".part")
            tmp.write_bytes(payload)
            tmp.replace(folder / name)
