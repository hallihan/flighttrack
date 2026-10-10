"""Generate the synthetic sample traffic shipped for replay mode.

The aircraft are invented: straight-line tracks placed so every selectable range shows
something. Run from the repo root:

    python scripts/make_sample_replay.py
"""

import json
from pathlib import Path

from flighttrack.geo import destination

CENTER = (43.6777, -79.6248)
INTERVAL_S = 5
FRAMES = 48  # a four-minute loop

OUTPUT = Path(__file__).resolve().parent.parent / "flighttrack/feeds/data/sample.json"

# hex, callsign, registration, type, start bearing/distance from center (deg, NM),
# track, ground speed (kt), altitude (ft or "ground"), vertical rate (fpm)
TRACKS = [
    ("f00001", "DEMO101", "X-DEMA", "B738", 250, 0.9, 70, 140, 1200, -700),
    ("f00002", "DEMO202", "X-DEMB", "C172", 20, 2.2, 200, 95, 2500, 0),
    ("f00003", "DEMO303", "X-DEMC", "A320", 130, 4.5, 310, 210, 6000, 1800),
    ("f00004", "DEMO404", "X-DEMD", "DH8D", 300, 9.0, 120, 240, 11000, -1200),
    ("f00005", "DEMO505", "X-DEME", "B77W", 60, 20.0, 240, 380, 24000, -1500),
    ("f00006", None, "X-DEMF", "PC12", 190, 28.0, 10, 260, 17000, 0),
    ("f00007", "DEMO707", "X-DEMG", "A359", 340, 36.0, 150, 470, 38000, 0),
    ("f00008", "DEMO808", "X-DEMH", "E190", 100, 46.0, 280, 430, 33000, 0),
    ("f00009", "DEMO909", "X-DEMI", "CRJ9", 180, 0.4, 60, 12, "ground", 0),
]


def frame(index: int) -> dict:
    elapsed_s = index * INTERVAL_S
    aircraft = []
    for hex_code, callsign, reg, type_code, brg, dist, track, gs, alt, rate in TRACKS:
        start = destination(*CENTER, brg, dist)
        lat, lon = destination(*start, track, gs * elapsed_s / 3600)
        record = {
            "hex": hex_code,
            "r": reg,
            "t": type_code,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "track": track,
            "gs": gs,
            "seen_pos": 0.5,
            "squawk": "1200",
            "emergency": "none",
        }
        if callsign:
            record["flight"] = f"{callsign:<8}"
        if alt == "ground":
            record["alt_baro"] = "ground"
        else:
            record["alt_baro"] = max(0, round(alt + rate * elapsed_s / 60))
            record["baro_rate"] = rate
        aircraft.append(record)
    return {"ac": aircraft}


def main() -> None:
    data = {
        "center": {"lat": CENTER[0], "lon": CENTER[1]},
        "interval": INTERVAL_S,
        "frames": [frame(i) for i in range(FRAMES)],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    print(f"wrote {OUTPUT} ({FRAMES} frames, {len(TRACKS)} aircraft)")


if __name__ == "__main__":
    main()
