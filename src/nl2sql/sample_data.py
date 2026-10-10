"""A synthetic bike-share database (stations + trips) with realistic usage patterns.

Members commute at 8am and 6pm on weekdays, casual riders ride midday and on
weekends, and e-bikes cover longer distances. Seeded, so results are reproducible.
"""
from __future__ import annotations

import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

NEIGHBORHOODS = {
    "Midtown": (40.754, -73.984), "Chelsea": (40.746, -74.001), "East Village": (40.726, -73.983),
    "Williamsburg": (40.714, -73.961), "Downtown Brooklyn": (40.693, -73.990), "Upper West Side": (40.787, -73.975),
}
STREETS = ["Broadway", "5 Ave", "Park Ave", "W 23 St", "E 14 St", "Bedford Ave", "Atlantic Ave", "Columbus Ave", "Union Sq", "Court St"]


def generate(out_dir: str | Path, n_trips: int = 15000, seed: int = 7) -> None:
    rng = random.Random(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stations = []
    sid = 100
    for hood, (lat, lon) in NEIGHBORHOODS.items():
        for k in range(5):
            sid += 1
            stations.append({"station_id": sid, "name": f"{rng.choice(STREETS)} & {hood} {k + 1}", "neighborhood": hood,
                             "capacity": rng.choice([19, 23, 27, 31, 39]),
                             "lat": round(lat + rng.uniform(-0.008, 0.008), 5), "lon": round(lon + rng.uniform(-0.008, 0.008), 5)})
    hubs = [s for s in stations if s["neighborhood"] in ("Midtown", "Chelsea")]
    start = datetime(2026, 6, 1)
    trips = []
    for i in range(1, n_trips + 1):
        rider = "member" if rng.random() < 0.72 else "casual"
        day = start + timedelta(days=rng.randrange(30))
        weekend = day.weekday() >= 5
        if rider == "member" and not weekend:
            hour = rng.choice([7, 8, 8, 8, 9, 12, 17, 17, 18, 18, 18, 19, 21])
        else:
            hour = rng.choice([10, 11, 12, 13, 14, 14, 15, 15, 16, 17, 19])
        if rider == "casual" and not weekend and rng.random() < 0.4:
            continue  # casual riding concentrates on weekends
        started = day + timedelta(hours=hour, minutes=rng.randrange(60), seconds=rng.randrange(60))
        a = rng.choice(hubs if rider == "member" and rng.random() < 0.5 else stations)
        b = rng.choice([s for s in stations if s["station_id"] != a["station_id"]])
        bike = "electric" if rng.random() < (0.55 if rider == "member" else 0.45) else "classic"
        dist_km = max(0.4, math.dist((a["lat"], a["lon"]), (b["lat"], b["lon"])) * 105 * rng.uniform(1.1, 1.4))
        speed = 15 if bike == "electric" else 11
        minutes = dist_km / speed * 60 * (1.0 if rider == "member" else rng.uniform(1.3, 2.2)) + rng.uniform(1, 4)
        trips.append({"trip_id": i, "start_station_id": a["station_id"], "end_station_id": b["station_id"],
                      "started_at": started.isoformat(sep=" "), "ended_at": (started + timedelta(minutes=minutes)).isoformat(sep=" ", timespec="seconds"),
                      "duration_min": round(minutes, 1), "distance_km": round(dist_km, 2), "rider_type": rider, "bike_type": bike})
    for name, rows in (("stations.csv", stations), ("trips.csv", trips)):
        with (out / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def connect(data_dir: str | Path) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with `stations` and `trips` loaded from the CSVs."""
    d = Path(data_dir)
    if not (d / "trips.csv").exists():
        generate(d)
    con = duckdb.connect()
    con.execute(f"CREATE TABLE stations AS SELECT * FROM read_csv_auto('{(d / 'stations.csv').as_posix()}')")
    con.execute(f"CREATE TABLE trips AS SELECT * FROM read_csv_auto('{(d / 'trips.csv').as_posix()}', timestampformat='%Y-%m-%d %H:%M:%S')")
    return con
