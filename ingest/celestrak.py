"""Fetch CelesTrak GP JSON, upsert satellites, log the sync.

Cache-first by default. Pass live=True (or --live) to hit the API and
refresh cache/celestrak.json.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "cache" / "celestrak.json"
DEBRIS_CACHE_PATH = ROOT / "cache" / "debris.json"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
GP_URL = "https://celestrak.org/NORAD/elements/gp.php"

# Small, typed slices — enough for a viva demo, small enough to cache.
CATALOGS = [
    {"group": "stations", "sat_type": "sci", "limit": 15, "operator": "International"},
    {"group": "gps-ops", "sat_type": "nav", "limit": 8, "operator": "US Space Force"},
    {"group": "iridium-NEXT", "sat_type": "comm", "limit": 8, "operator": "Iridium Communications"},
    {"group": "planet", "sat_type": "eo", "limit": 8, "operator": "Planet Labs"},
    {"group": "science", "sat_type": "sci", "limit": 8, "operator": "Research"},
]
DEBRIS_GROUP = "iridium-33-debris"
DEBRIS_LIMIT = 8

MU_KM3_S2 = 398600.4418
RE_KM = 6378.137

# Make app/ importable when running `python -m ingest.celestrak`
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _headers() -> dict:
    return {"User-Agent": UA, "Accept": "application/json"}


def _fetch_group(group: str) -> list:
    resp = requests.get(
        GP_URL,
        params={"GROUP": group, "FORMAT": "json"},
        headers=_headers(),
        timeout=45,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected CelesTrak payload for group {group}")
    return data


def fetch_live() -> tuple[list[dict], list[dict]]:
    """Pull a few CelesTrak groups and write the local cache."""
    records: list[dict] = []
    seen: set[str] = set()
    for spec in CATALOGS:
        raw = _fetch_group(spec["group"])
        for row in raw[: spec["limit"]]:
            norad = str(row.get("NORAD_CAT_ID") or "").strip()
            if not norad or norad in seen:
                continue
            seen.add(norad)
            records.append({**row, "_ois_type": spec["sat_type"], "_ois_operator": spec["operator"],
                            "_ois_group": spec["group"]})
    debris = _fetch_group(DEBRIS_GROUP)[:DEBRIS_LIMIT]
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")
    DEBRIS_CACHE_PATH.write_text(json.dumps(debris, indent=2), encoding="utf-8")
    return records, debris


def load_cache() -> tuple[list[dict], list[dict]]:
    if not CACHE_PATH.exists():
        raise FileNotFoundError(
            f"No cache at {CACHE_PATH}. Run a live sync once: "
            "python -m ingest.celestrak --live"
        )
    records = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    debris: list[dict] = []
    if DEBRIS_CACHE_PATH.exists():
        debris = json.loads(DEBRIS_CACHE_PATH.read_text(encoding="utf-8"))
    return records, debris


def load_catalog(live: bool) -> tuple[list[dict], list[dict], str]:
    if live or not CACHE_PATH.exists():
        records, debris = fetch_live()
        return records, debris, "celestrak:live"
    records, debris = load_cache()
    return records, debris, "celestrak:cache"


def _parse_epoch(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    text = value.replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _launch_date(object_id: str | None):
    if not object_id or len(object_id) < 4 or not object_id[:4].isdigit():
        return None
    return f"{object_id[:4]}-01-01"


def _station_operator(name: str, fallback: str) -> str:
    upper = name.upper()
    if "ISS" in upper or "ZARYA" in upper:
        return "NASA / Roscosmos"
    if "TIANHE" in upper or "CSS" in upper or "SZ-" in upper or "SHENZHOU" in upper:
        return "CNSA"
    if "HST" in upper or "HUBBLE" in upper:
        return "NASA"
    return fallback


def kepler_elements(row: dict) -> tuple[float, float, float, float, datetime]:
    """Inclination, eccentricity, apogee km, perigee km, epoch from GP JSON."""
    inc = float(row.get("INCLINATION") or 0)
    ecc = float(row.get("ECCENTRICITY") or 0)
    mean_motion = float(row.get("MEAN_MOTION") or 0)  # rev / day
    epoch = _parse_epoch(row.get("EPOCH"))
    if mean_motion <= 0:
        return inc, ecc, 0.0, 0.0, epoch
    n = mean_motion * 2.0 * math.pi / 86400.0
    a = (MU_KM3_S2 / (n * n)) ** (1.0 / 3.0)
    apogee = a * (1.0 + ecc) - RE_KM
    perigee = a * (1.0 - ecc) - RE_KM
    return inc, ecc, round(apogee, 2), round(perigee, 2), epoch


def approx_latlonalt(row: dict) -> tuple[float, float, float]:
    """Rough ECI→geodetic conversion. Not SGP4; good enough for the course."""
    inc = math.radians(float(row.get("INCLINATION") or 0))
    raan = math.radians(float(row.get("RA_OF_ASC_NODE") or 0))
    argp = math.radians(float(row.get("ARG_OF_PERICENTER") or 0))
    m_anom = math.radians(float(row.get("MEAN_ANOMALY") or 0))
    ecc = float(row.get("ECCENTRICITY") or 0)
    mean_motion = float(row.get("MEAN_MOTION") or 0)
    if mean_motion <= 0:
        return 0.0, 0.0, 0.0
    n = mean_motion * 2.0 * math.pi / 86400.0
    a = (MU_KM3_S2 / (n * n)) ** (1.0 / 3.0)
    nu = m_anom  # small-e approximation: true anomaly ≈ mean anomaly
    u = argp + nu
    r = a * (1.0 - ecc * math.cos(nu))
    x = r * (math.cos(raan) * math.cos(u) - math.sin(raan) * math.sin(u) * math.cos(inc))
    y = r * (math.sin(raan) * math.cos(u) + math.cos(raan) * math.sin(u) * math.cos(inc))
    z = r * (math.sin(u) * math.sin(inc))
    lat = math.degrees(math.atan2(z, math.sqrt(x * x + y * y)))
    lon = math.degrees(math.atan2(y, x))
    alt = math.sqrt(x * x + y * y + z * z) - RE_KM
    return round(lat, 6), round(lon, 6), round(alt, 2)


def _upsert_type(cursor, satellite_id: int, sat_type: str, name: str) -> None:
    cursor.execute("DELETE FROM Comm_Satellite WHERE Satellite_ID = %s", (satellite_id,))
    cursor.execute("DELETE FROM Nav_Satellite WHERE Satellite_ID = %s", (satellite_id,))
    cursor.execute("DELETE FROM EO_Satellite WHERE Satellite_ID = %s", (satellite_id,))
    cursor.execute("DELETE FROM Sci_Satellite WHERE Satellite_ID = %s", (satellite_id,))
    if sat_type == "comm":
        cursor.execute(
            "INSERT INTO Comm_Satellite (Satellite_ID, Band, Transponders) VALUES (%s, %s, %s)",
            (satellite_id, "L-band", 48),
        )
    elif sat_type == "nav":
        cursor.execute(
            "INSERT INTO Nav_Satellite (Satellite_ID, Constellation, Signal_Type) VALUES (%s, %s, %s)",
            (satellite_id, "GPS", "L1/L2"),
        )
    elif sat_type == "eo":
        cursor.execute(
            "INSERT INTO EO_Satellite (Satellite_ID, Sensor, Resolution_m) VALUES (%s, %s, %s)",
            (satellite_id, "Optical", 3.70),
        )
    elif sat_type == "sci":
        cursor.execute(
            "INSERT INTO Sci_Satellite (Satellite_ID, Mission, Instrument) VALUES (%s, %s, %s)",
            (satellite_id, name[:80], "Various"),
        )


def sync(live: bool = False) -> dict:
    from app.db import get_connection

    records, debris, source = load_catalog(live)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    upserted = 0
    history = 0
    debris_rows = 0
    status = "success"

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            for row in records:
                norad = str(row.get("NORAD_CAT_ID") or "").strip()
                name = (row.get("OBJECT_NAME") or f"NORAD {norad}").strip()
                operator = row.get("_ois_operator") or "Unknown"
                if row.get("_ois_group") == "stations":
                    operator = _station_operator(name, operator)
                launch = _launch_date(row.get("OBJECT_ID"))
                cursor.execute(
                    """
                    INSERT INTO Satellite (NORAD_ID, Name, Operator, Status, Launch_Date)
                    VALUES (%s, %s, %s, 'active', %s)
                    ON DUPLICATE KEY UPDATE
                        Name = VALUES(Name),
                        Operator = VALUES(Operator),
                        Status = 'active',
                        Launch_Date = COALESCE(VALUES(Launch_Date), Launch_Date)
                    """,
                    (norad, name, operator, launch),
                )
                cursor.execute(
                    "SELECT Satellite_ID FROM Satellite WHERE NORAD_ID = %s", (norad,)
                )
                satellite_id = cursor.fetchone()[0]
                inc, ecc, apogee, perigee, epoch = kepler_elements(row)
                cursor.execute(
                    """
                    INSERT INTO Orbit_Parameters
                        (Satellite_ID, Inclination, Eccentricity, Apogee_km, Perigee_km, Epoch)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        Inclination = VALUES(Inclination),
                        Eccentricity = VALUES(Eccentricity),
                        Apogee_km = VALUES(Apogee_km),
                        Perigee_km = VALUES(Perigee_km),
                        Epoch = VALUES(Epoch)
                    """,
                    (satellite_id, inc, ecc, apogee, perigee, epoch),
                )
                lat, lon, alt = approx_latlonalt(row)
                cursor.execute(
                    """
                    INSERT INTO Position_History
                        (Satellite_ID, Observed_At, Lat, Lon, Alt_km)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (satellite_id, now, lat, lon, alt),
                )
                _upsert_type(cursor, satellite_id, row.get("_ois_type") or "", name)
                upserted += 1
                history += 1

            for row in debris:
                catalog_id = str(row.get("NORAD_CAT_ID") or "").strip()
                if not catalog_id:
                    continue
                epoch = _parse_epoch(row.get("EPOCH"))
                cursor.execute(
                    """
                    INSERT INTO Debris_Records (Parent_Satellite_ID, Catalog_ID, First_Seen, Status)
                    VALUES (NULL, %s, %s, 'tracked')
                    ON DUPLICATE KEY UPDATE First_Seen = VALUES(First_Seen), Status = 'tracked'
                    """,
                    (catalog_id, epoch),
                )
                debris_rows += 1

            cursor.execute(
                """
                INSERT INTO Sync_Log (Synced_At, Source, Rows_Upserted, Status)
                VALUES (%s, %s, %s, %s)
                """,
                (now, source, upserted, status),
            )
            cursor.close()
    except Exception:
        status = "failed"
        try:
            from app.db import get_connection as _gc

            with _gc() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO Sync_Log (Synced_At, Source, Rows_Upserted, Status)
                    VALUES (%s, %s, %s, 'failed')
                    """,
                    (now, source, upserted),
                )
                cursor.close()
        except Exception:
            pass
        raise

    return {
        "source": source,
        "upserted": upserted,
        "history": history,
        "debris": debris_rows,
        "status": status,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OIS CelesTrak ingest")
    parser.add_argument("--live", action="store_true", help="Fetch CelesTrak and refresh cache")
    parser.add_argument("--print-json", action="store_true", help="Print records; do not write the DB")
    args = parser.parse_args(argv)

    if args.print_json:
        records, debris, source = load_catalog(args.live)
        print(f"# source={source} satellites={len(records)} debris={len(debris)}")
        print(json.dumps(records[:3], indent=2))
        return 0

    result = sync(live=args.live)
    print(
        f"Sync {result['status']}: {result['upserted']} satellites, "
        f"{result['history']} history rows, {result['debris']} debris "
        f"({result['source']})"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
    except requests.RequestException as exc:
        print(f"CelesTrak request failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
