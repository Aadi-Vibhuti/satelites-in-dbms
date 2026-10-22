"""Multi-source space & orbital telemetry ingestion engine.

Integrates:
- CelesTrak GP API (General Perturbations orbital elements)
- SatNOGS Satellite Database (Open global satellite registry)
- WhereTheISS API (Real-time live telemetry coordinates for ISS)
- NASA NeoWs API (Near-Earth Objects & close proximity orbital debris)
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_config
from app.db import get_connection

# Common realistic user-agent to prevent WAF / Cloudflare 403 or timeouts
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php"
SATNOGS_URL = "https://db.satnogs.org/api/satellites/"
WHERETHEISS_URL = "https://api.wheretheiss.at/v1/satellites/25544"
NASA_NEOWS_URL = "https://api.nasa.gov/neo/rest/v1/feed"

CACHE_DIR = ROOT / "cache"


def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
    })
    return session


def fetch_celestrak_groups(
    groups: list[dict],
    session: requests.Session | None = None,
    timeout: int = 7,
    control_callback: Callable[[], None] | None = None,
) -> list[dict]:
    """Fetch CelesTrak GP data, failing immediately on HTTP or payload errors."""
    s = session or get_session()
    records: list[dict] = []
    seen: set[str] = set()

    for spec in groups:
        if control_callback:
            control_callback()
        resp = s.get(
            CELESTRAK_URL,
            params={"GROUP": spec["group"], "FORMAT": "JSON"},
            timeout=timeout,
            allow_redirects=False,
        )
        if resp.status_code != 200:
            detail = " ".join(resp.text.split())[:240]
            raise requests.HTTPError(
                f"CelesTrak returned HTTP {resp.status_code} for group "
                f"{spec['group']}: {detail or resp.reason}",
                response=resp,
            )
        data = resp.json()
        if not isinstance(data, list):
            raise ValueError(f"Unexpected CelesTrak response for group {spec['group']}")
        for row in data[: spec.get("limit", 10)]:
            norad = str(row.get("NORAD_CAT_ID") or "").strip()
            if not norad or norad in seen:
                continue
            seen.add(norad)
            records.append({
                **row,
                "_ois_type": spec.get("sat_type", "untyped"),
                "_ois_operator": spec.get("operator", "International"),
                "_ois_group": spec.get("group", ""),
            })

    return records


def fetch_satnogs_catalog(
    limit: int | None = None,
    session: requests.Session | None = None,
    control_callback: Callable[[], None] | None = None,
) -> list[dict]:
    """Fetch the paginated public satellite catalog from SatNOGS."""
    s = session or get_session()
    url: str | None = SATNOGS_URL
    params: dict[str, Any] | None = {"format": "json", "limit": 1000}
    items: list[dict] = []
    while url and len(items) < 20000:
        if control_callback:
            control_callback()
        resp = s.get(url, params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            items.extend(payload)
            break
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("Unexpected SatNOGS response; expected a list or paginated results")
        items.extend(payload["results"])
        next_url = payload.get("next")
        url = urljoin(resp.url, next_url) if next_url else None
        params = None

    if limit is not None:
        items = items[:limit]
    results = []
    for item in items:
        norad = str(item.get("norad_cat_id") or "").strip()
        if not norad or norad == "None":
            continue
        status_raw = str(item.get("status") or "").lower()
        status = "active" if "alive" in status_raw or "orbit" in status_raw else "inactive"
        results.append({
            "NORAD_CAT_ID": norad,
            "OBJECT_NAME": item.get("name") or f"SATNOGS-{norad}",
            "_ois_operator": item.get("operator") or item.get("countries") or "International",
            "_ois_status": status,
            "_ois_type": "comm" if "transmitters" in item else "sci",
            "INCLINATION": 51.6,
            "ECCENTRICITY": 0.001,
            "APOGEE": 500.0,
            "PERIGEE": 480.0,
        })
    return results


def fetch_n2yo_catalog(
    api_key: str,
    session: requests.Session | None = None,
    control_callback: Callable[[], None] | None = None,
) -> list[dict]:
    """Fetch TLEs for a small representative set of satellites from N2YO."""
    if not api_key:
        raise ValueError("N2YO requires an API key. Add it in Settings.")
    s = session or get_session()
    records = []
    for norad in (25544, 20580, 33591):
        if control_callback:
            control_callback()
        resp = s.get(
            f"https://api.n2yo.com/rest/v1/satellite/tle/{norad}",
            params={"apiKey": api_key},
            timeout=10,
        )
        if resp.status_code != 200:
            raise requests.HTTPError(
                f"N2YO returned HTTP {resp.status_code}: {' '.join(resp.text.split())[:200]}",
                response=resp,
            )
        payload = resp.json()
        info = payload.get("info", {})
        lines = str(payload.get("tle") or "").splitlines()
        if len(lines) < 2:
            raise ValueError(f"N2YO returned no TLE for NORAD {norad}")
        line2 = lines[1]
        try:
            inclination = float(line2[8:16])
            eccentricity = float(f"0.{line2[26:33].strip()}")
            mean_motion = float(line2[52:63])
            semi_major_axis = (398600.4418 / (mean_motion * 2 * math.pi / 86400) ** 2) ** (1 / 3)
            apogee = semi_major_axis * (1 + eccentricity) - 6378.137
            perigee = semi_major_axis * (1 - eccentricity) - 6378.137
        except (ValueError, ZeroDivisionError):
            inclination, eccentricity, apogee, perigee = 51.6, 0.001, 500.0, 480.0
        records.append({
            "NORAD_CAT_ID": str(norad),
            "OBJECT_NAME": info.get("satname") or f"NORAD {norad}",
            "INCLINATION": inclination,
            "ECCENTRICITY": eccentricity,
            "APOGEE": apogee,
            "PERIGEE": perigee,
            "_ois_operator": "N2YO",
            "_ois_type": "untyped",
        })
    return records


def fetch_spacetrack_catalog(
    identity: str,
    password: str,
    session: requests.Session | None = None,
    control_callback: Callable[[], None] | None = None,
) -> list[dict]:
    """Authenticate to Space-Track and fetch current, propagable GP records."""
    if not identity or not password:
        raise ValueError("Space-Track requires account credentials. Add them in Settings.")
    if control_callback:
        control_callback()
    s = session or get_session()
    login = s.post(
        "https://www.space-track.org/ajaxauth/login",
        data={"identity": identity, "password": password},
        timeout=15,
    )
    login.raise_for_status()
    try:
        login_data = login.json()
    except ValueError:
        login_data = None
    if isinstance(login_data, dict) and login_data.get("Login") == "Failed":
        raise ValueError("Space-Track login failed. Check the account in Settings.")
    query_url = (
        "https://www.space-track.org/basicspacedata/query/class/gp/"
        "DECAY_DATE/null-val/EPOCH/%3Enow-10/format/json"
    )
    if control_callback:
        control_callback()
    resp = s.get(query_url, timeout=30)
    if resp.status_code != 200:
        raise requests.HTTPError(
            f"Space-Track returned HTTP {resp.status_code}: {' '.join(resp.text.split())[:200]}",
            response=resp,
        )
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected Space-Track GP response")
    for row in data:
        row.setdefault("_ois_type", "untyped")
        row.setdefault("_ois_operator", "Space-Track")
    return data


def fetch_live_iss_position(session: requests.Session | None = None) -> dict | None:
    """Fetch live real-time orbital coordinates for the International Space Station."""
    s = session or get_session()
    resp = s.get(WHERETHEISS_URL, timeout=8)
    resp.raise_for_status()
    data = resp.json()
    return {
        "norad_id": "25544",
        "name": "ISS (ZARYA)",
        "lat": float(data.get("latitude", 0.0)),
        "lon": float(data.get("longitude", 0.0)),
        "alt_km": float(data.get("altitude", 420.0)),
        "velocity_kmh": float(data.get("velocity", 27600.0)),
        "observed_at": datetime.fromtimestamp(data.get("timestamp", datetime.now().timestamp()), timezone.utc).replace(tzinfo=None),
    }


def fetch_nasa_neows_debris(api_key: str = "DEMO_KEY", session: requests.Session | None = None) -> list[dict]:
    """Fetch Near-Earth Objects (asteroids / debris) from NASA NeoWs API."""
    s = session or get_session()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    resp = s.get(
        NASA_NEOWS_URL,
        params={"start_date": today, "end_date": today, "api_key": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    near_earth = data.get("near_earth_objects", {}).get(today, [])
    results = []
    for obj in near_earth[:10]:
        results.append({
            "name": obj.get("name") or "NEO-DEBRIS",
            "estimated_diameter_m": obj.get("estimated_diameter", {}).get("meters", {}).get("estimated_diameter_max", 10.0),
            "is_potentially_hazardous": obj.get("is_potentially_hazardous_asteroid", False),
            "close_approach_date": today,
        })
    return results


def run_multi_source_sync(
    source: str = "all",
    live: bool = True,
    progress_callback: Callable[[int, int, str, float], None] | None = None,
    control_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Execute multi-step telemetry ingestion with live progress updates."""
    total_steps = 5

    def report(step: int, msg: str, pct: float) -> None:
        if control_callback:
            control_callback()
        if progress_callback:
            progress_callback(step, total_steps, msg, pct)

    # Step 1: Session init
    report(1, "Initializing network sessions and configuring API endpoints...", 0.15)
    cfg = load_config()
    session = get_session()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    celestrak_specs = [
        {"group": "active", "sat_type": "untyped", "limit": 20000, "operator": "International"},
    ]

    # Step 2: Fetching satellite catalog
    report(2, f"Connecting to space telemetry APIs (Source: {source.upper()})...", 0.35)
    catalog_records: list[dict] = []
    sources_used: list[str] = []
    source_errors: list[str] = []

    if live:
        if source in ("all", "celestrak"):
            cache_file = CACHE_DIR / "celestrak.json"
            try:
                cache_fresh = cache_file.exists() and (
                    datetime.now(timezone.utc).timestamp() - cache_file.stat().st_mtime < 2 * 60 * 60
                )
                if cache_fresh:
                    cel_data = json.loads(cache_file.read_text(encoding="utf-8"))
                    cache_fresh = (
                        isinstance(cel_data, list)
                        and len(cel_data) >= 1000
                        and any(row.get("_ois_group") == "active" for row in cel_data)
                    )
                if cache_fresh:
                    sources_used.append("CelesTrak GP (fresh local cache)")
                else:
                    cel_data = fetch_celestrak_groups(
                        celestrak_specs,
                        session=session,
                        control_callback=control_callback,
                    )
                    if cel_data:
                        cache_file.parent.mkdir(parents=True, exist_ok=True)
                        cache_file.write_text(json.dumps(cel_data, indent=2), encoding="utf-8")
                        sources_used.append("CelesTrak GP")
                catalog_records.extend(cel_data)
            except Exception as exc:
                source_errors.append(f"CelesTrak: {exc}")
                if source == "celestrak":
                    raise RuntimeError(f"CelesTrak fetch failed: {exc}") from exc

        if source in ("all", "satnogs"):
            try:
                satnogs_data = fetch_satnogs_catalog(
                    session=session,
                    control_callback=control_callback,
                )
                if satnogs_data:
                    catalog_records.extend(satnogs_data)
                    sources_used.append("SatNOGS DB")
            except Exception as exc:
                source_errors.append(f"SatNOGS DB: {exc}")
                if source == "satnogs":
                    raise RuntimeError(f"SatNOGS fetch failed: {exc}") from exc

        if source == "n2yo" or (source == "all" and cfg.get("n2yo_api_key")):
            try:
                n2yo_data = fetch_n2yo_catalog(
                    cfg.get("n2yo_api_key", ""),
                    session=session,
                    control_callback=control_callback,
                )
                catalog_records.extend(n2yo_data)
                sources_used.append("N2YO")
            except Exception as exc:
                source_errors.append(f"N2YO: {exc}")
                if source == "n2yo":
                    raise RuntimeError(f"N2YO fetch failed: {exc}") from exc

        st_identity = cfg.get("spacetrack_identity", "")
        st_password = cfg.get("spacetrack_password", "")
        if source == "spacetrack" or (source == "all" and st_identity and st_password):
            st_cache = CACHE_DIR / "spacetrack.json"
            st_cache_fresh = st_cache.exists() and (
                datetime.now(timezone.utc).timestamp() - st_cache.stat().st_mtime < 60 * 60
            )
            try:
                if st_cache_fresh:
                    st_data = json.loads(st_cache.read_text(encoding="utf-8"))
                    sources_used.append("Space-Track (fresh local cache)")
                else:
                    st_data = fetch_spacetrack_catalog(
                        st_identity,
                        st_password,
                        session=session,
                        control_callback=control_callback,
                    )
                    st_cache.parent.mkdir(parents=True, exist_ok=True)
                    st_cache.write_text(json.dumps(st_data, indent=2), encoding="utf-8")
                    sources_used.append("Space-Track")
                catalog_records.extend(st_data)
            except Exception as exc:
                source_errors.append(f"Space-Track: {exc}")
                if source == "spacetrack":
                    raise RuntimeError(f"Space-Track fetch failed: {exc}") from exc

    demo_debris: list[dict] = []
    if not catalog_records and (source in ("all", "cache") or not live):
        # Fallback to cache
        cache_file = CACHE_DIR / "celestrak.json"
        if cache_file.exists():
            catalog_records = json.loads(cache_file.read_text(encoding="utf-8"))
            sources_used.append("Local Cache")
        if source == "cache":
            demo_file = CACHE_DIR / "demo_records.json"
            if demo_file.exists():
                demo_records = json.loads(demo_file.read_text(encoding="utf-8"))
                if isinstance(demo_records, list):
                    catalog_records.extend(demo_records)
                    sources_used.append("Analysis Demo Records")
            debris_file = CACHE_DIR / "demo_debris.json"
            if debris_file.exists():
                demo_debris = json.loads(debris_file.read_text(encoding="utf-8"))

    # Step 3: Fetching real-time telemetry (WhereTheISS & NASA)
    report(3, "Querying live real-time position telemetry (ISS vector & NASA NEOs)...", 0.55)
    live_iss = None
    if live and source in ("all", "open_notify"):
        try:
            live_iss = fetch_live_iss_position(session=session)
            if live_iss:
                sources_used.append("WhereTheISS")
        except Exception as exc:
            source_errors.append(f"WhereTheISS: {exc}")
            if source == "open_notify":
                raise RuntimeError(f"WhereTheISS fetch failed: {exc}") from exc

    nasa_debris = []
    if live and source in ("all", "nasa"):
        try:
            nasa_debris = fetch_nasa_neows_debris(api_key=cfg.get("nasa_api_key", "DEMO_KEY"), session=session)
        except Exception as exc:
            source_errors.append(f"NASA NeoWs: {exc}")
            if source == "nasa":
                raise RuntimeError(f"NASA NeoWs fetch failed: {exc}") from exc
    if nasa_debris:
        sources_used.append("NASA NeoWs")

    # Step 4: Database Upsert
    report(4, f"Upserting {len(catalog_records)} spacecraft records into MariaDB...", 0.80)
    upserted_count = 0
    history_count = 0
    debris_count = 0

    try:
        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)

        with get_connection() as conn:
            cursor = conn.cursor()
            for rec in catalog_records:
                if control_callback:
                    control_callback()
                norad = str(rec.get("NORAD_CAT_ID") or rec.get("NORAD_ID") or "").strip()
                if not norad:
                    continue
                name = str(rec.get("OBJECT_NAME") or rec.get("Name") or f"NORAD {norad}").strip()
                operator = rec.get("_ois_operator") or "International"
                sat_type = rec.get("_ois_type") or "untyped"
                status = rec.get("_ois_status") or "active"

                cursor.execute(
                    """
                    INSERT INTO Satellite (NORAD_ID, Name, Operator, Status)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        Name = VALUES(Name),
                        Operator = VALUES(Operator),
                        Status = VALUES(Status)
                    """,
                    (norad, name, operator, status),
                )
                cursor.execute("SELECT Satellite_ID FROM Satellite WHERE NORAD_ID = %s", (norad,))
                row_sat = cursor.fetchone()
                if not row_sat:
                    continue
                sat_id = row_sat[0]
                upserted_count += 1

                # Orbit parameters; demo records can omit this to exercise data-gap scans.
                if not rec.get("_ois_missing_orbit"):
                    try:
                        inc = float(rec.get("INCLINATION") or 51.6)
                        ecc = float(rec.get("ECCENTRICITY") or 0.001)
                        apo_value = rec.get("APOGEE") or rec.get("APOAPSIS")
                        peri_value = rec.get("PERIGEE") or rec.get("PERIAPSIS")
                        if apo_value is None or peri_value is None:
                            semi_major_axis = float(rec.get("SEMIMAJOR_AXIS") or 0)
                            earth_radius_km = 6378.137
                            apo_value = semi_major_axis * (1 + ecc) - earth_radius_km if semi_major_axis else 500.0
                            peri_value = semi_major_axis * (1 - ecc) - earth_radius_km if semi_major_axis else 480.0
                        apo = float(apo_value)
                        peri = float(peri_value)
                    except (ValueError, TypeError):
                        inc, ecc, apo, peri = 51.6, 0.001, 500.0, 480.0

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
                        (sat_id, inc, ecc, apo, peri, now_dt),
                    )

                # Subclass specialization
                if sat_type == "comm":
                    cursor.execute(
                        "INSERT INTO Comm_Satellite (Satellite_ID, Band, Transponders) VALUES (%s, 'Ku', 24) ON DUPLICATE KEY UPDATE Band=VALUES(Band), Transponders=VALUES(Transponders)",
                        (sat_id,),
                    )
                elif sat_type == "nav":
                    cursor.execute(
                        "INSERT INTO Nav_Satellite (Satellite_ID, Constellation, Signal_Type) VALUES (%s, 'GPS', 'L1/L2') ON DUPLICATE KEY UPDATE Constellation=VALUES(Constellation), Signal_Type=VALUES(Signal_Type)",
                        (sat_id,),
                    )
                elif sat_type == "eo":
                    cursor.execute(
                        "INSERT INTO EO_Satellite (Satellite_ID, Sensor, Resolution_m) VALUES (%s, 'Multispectral', 3.0) ON DUPLICATE KEY UPDATE Sensor=VALUES(Sensor), Resolution_m=VALUES(Resolution_m)",
                        (sat_id,),
                    )
                elif sat_type == "sci":
                    cursor.execute(
                        "INSERT INTO Sci_Satellite (Satellite_ID, Mission, Instrument) VALUES (%s, %s, 'Spectrometer') ON DUPLICATE KEY UPDATE Mission=VALUES(Mission), Instrument=VALUES(Instrument)",
                        (sat_id, name),
                    )

                for sample in rec.get("_ois_demo_positions", []):
                    cursor.execute(
                        """
                        INSERT INTO Position_History (Satellite_ID, Observed_At, Lat, Lon, Alt_km)
                        SELECT %s, %s, %s, %s, %s FROM DUAL
                        WHERE NOT EXISTS (
                            SELECT 1 FROM Position_History
                            WHERE Satellite_ID = %s AND Observed_At = %s
                        )
                        """,
                        (sat_id, sample["observed_at"], sample["lat"], sample["lon"], sample["alt_km"],
                         sat_id, sample["observed_at"]),
                    )
                    history_count += cursor.rowcount

            # Insert real live ISS coordinates if available
            if live_iss:
                cursor.execute("SELECT Satellite_ID FROM Satellite WHERE NORAD_ID = '25544'")
                iss_row = cursor.fetchone()
                if iss_row:
                    iss_id = iss_row[0]
                    cursor.execute(
                        """
                        INSERT INTO Position_History (Satellite_ID, Observed_At, Lat, Lon, Alt_km)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (iss_id, live_iss["observed_at"], live_iss["lat"], live_iss["lon"], live_iss["alt_km"]),
                    )
                    history_count += 1

            # Insert NASA debris if fetched
            for idx, d in enumerate(nasa_debris):
                if control_callback:
                    control_callback()
                cat_id = str(d.get("name") or f"NEO-{idx+1}").replace("(", "").replace(")", "").strip()[:30]
                cursor.execute(
                    """
                    INSERT INTO Debris_Records (Parent_Satellite_ID, Catalog_ID, First_Seen, Status)
                    VALUES (NULL, %s, %s, 'tracked')
                    ON DUPLICATE KEY UPDATE
                        Parent_Satellite_ID = VALUES(Parent_Satellite_ID),
                        First_Seen = VALUES(First_Seen),
                        Status = VALUES(Status)
                    """,
                    (cat_id, now_dt),
                )
                debris_count += 1

            for d in demo_debris:
                cursor.execute(
                    """
                    INSERT INTO Debris_Records (Parent_Satellite_ID, Catalog_ID, First_Seen, Status)
                    VALUES (NULL, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE First_Seen=VALUES(First_Seen), Status=VALUES(Status)
                    """,
                    (d["catalog_id"], d["first_seen"], d.get("status", "tracked")),
                )
                debris_count += 1

            # Log sync
            src_str = ", ".join(sources_used) if sources_used else "cache"
            cursor.execute(
                """
                INSERT INTO Sync_Log (Synced_At, Source, Rows_Upserted, Status)
                VALUES (%s, %s, %s, 'success')
                """,
                (now_dt, f"multi-api: {src_str}", upserted_count),
            )
            cursor.close()

    except Exception as exc:
        report(4, f"Database error during upsert: {exc}", 0.85)
        raise RuntimeError(f"Database error during upsert: {exc}") from exc

    # Step 5: Proximity Scan
    report(5, "Running ECEF proximity collision scan and logging conjunctions...", 0.95)
    from app import queries
    conjunctions = queries.generate_collision_alerts()

    report(5, "Ingestion and orbital calculations complete!", 1.0)

    return {
        "status": "success",
        "upserted": upserted_count,
        "history": history_count,
        "debris": debris_count,
        "conjunctions": len(conjunctions),
        "sources": sources_used,
        "source_errors": source_errors,
        "live_iss": live_iss is not None,
    }
