"""Build the bundled airport and runway tables from the OurAirports dataset (public domain).

Download airports.csv and runways.csv from https://davidmegginson.github.io/ourairports-data/
then run from the repo root:

    python scripts/make_airports.py path/to/airports.csv path/to/runways.csv

Keeps airports and seaplane bases (not heliports, balloonports or closed fields) and only the
columns needed to turn a code into a place. Keeps open runways at those airports that have
both ends located.
"""

import csv
import gzip
import io
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "flighttrack/data"
KEEP_TYPES = {"large_airport", "medium_airport", "small_airport", "seaplane_base"}
# Larger airports first, so that where two share a code the bigger one wins the lookup.
TYPE_ORDER = ["large_airport", "medium_airport", "small_airport", "seaplane_base"]
# One letter per kind in the table: Large, Medium, Small, Water.
SIZE_CODES = {
    "large_airport": "L",
    "medium_airport": "M",
    "small_airport": "S",
    "seaplane_base": "W",
}


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the output byte-identical for identical input.
    with (
        gzip.GzipFile(path, "wb", mtime=0) as raw,
        io.TextIOWrapper(raw, encoding="utf-8", newline="") as text,
    ):
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    print(f"wrote {path}: {len(rows)} rows, {path.stat().st_size / 1024:.0f} KB")


def main(airports_csv: str, runways_csv: str) -> None:
    with open(airports_csv, encoding="utf-8", newline="") as handle:
        airports = [row for row in csv.DictReader(handle) if row["type"] in KEEP_TYPES]
    airports.sort(key=lambda row: (TYPE_ORDER.index(row["type"]), row["ident"]))
    _write(
        DATA_DIR / "airports.csv.gz",
        ["ident", "icao", "iata", "local", "name", "place", "lat", "lon", "size", "elevation_ft"],
        [
            [
                row["ident"],
                row["icao_code"] or row["gps_code"],
                row["iata_code"],
                row["local_code"],
                row["name"],
                ", ".join(p for p in (row["municipality"], row["iso_country"]) if p),
                f"{float(row['latitude_deg']):.4f}",
                f"{float(row['longitude_deg']):.4f}",
                SIZE_CODES[row["type"]],
                row["elevation_ft"],
            ]
            for row in airports
        ],
    )

    kept = {row["ident"] for row in airports}
    with open(runways_csv, encoding="utf-8", newline="") as handle:
        runways = [
            row
            for row in csv.DictReader(handle)
            if row["airport_ident"] in kept
            and row["closed"] != "1"
            and row["le_latitude_deg"]
            and row["le_longitude_deg"]
            and row["he_latitude_deg"]
            and row["he_longitude_deg"]
        ]
    runways.sort(key=lambda row: (row["airport_ident"], row["le_ident"]))
    _write(
        DATA_DIR / "runways.csv.gz",
        [
            "airport",
            "le_ident",
            "le_lat",
            "le_lon",
            "he_ident",
            "he_lat",
            "he_lon",
            "width_ft",
            "length_ft",
        ],
        [
            [
                row["airport_ident"],
                row["le_ident"],
                f"{float(row['le_latitude_deg']):.5f}",
                f"{float(row['le_longitude_deg']):.5f}",
                row["he_ident"],
                f"{float(row['he_latitude_deg']):.5f}",
                f"{float(row['he_longitude_deg']):.5f}",
                row["width_ft"] or "",
                row["length_ft"] or "",
            ]
            for row in runways
        ],
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
