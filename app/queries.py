"""Read-side use cases: view, search, alerts, reports, projection."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from app.db import get_connection

RE_KM = 6378.137

TYPE_SQL = """
CASE
    WHEN c.Satellite_ID IS NOT NULL THEN 'comm'
    WHEN n.Satellite_ID IS NOT NULL THEN 'nav'
    WHEN e.Satellite_ID IS NOT NULL THEN 'eo'
    WHEN sci.Satellite_ID IS NOT NULL THEN 'sci'
    ELSE 'untyped'
END
"""

TYPE_JOINS = """
LEFT JOIN Comm_Satellite c   ON c.Satellite_ID = s.Satellite_ID
LEFT JOIN Nav_Satellite n    ON n.Satellite_ID = s.Satellite_ID
LEFT JOIN EO_Satellite e     ON e.Satellite_ID = s.Satellite_ID
LEFT JOIN Sci_Satellite sci  ON sci.Satellite_ID = s.Satellite_ID
"""


def _dicts(cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _ecef(lat: float, lon: float, alt_km: float) -> tuple[float, float, float]:
    lat_r = math.radians(float(lat))
    lon_r = math.radians(float(lon))
    r = RE_KM + float(alt_km)
    x = r * math.cos(lat_r) * math.cos(lon_r)
    y = r * math.cos(lat_r) * math.sin(lon_r)
    z = r * math.sin(lat_r)
    return x, y, z


def _distance_km(a: dict, b: dict) -> float:
    ax, ay, az = _ecef(a["Lat"], a["Lon"], a["Alt_km"])
    bx, by, bz = _ecef(b["Lat"], b["Lon"], b["Alt_km"])
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)


def _probability(distance_km: float) -> float:
    """Dummy conjunction score: closer → higher percent. Not real TCA physics."""
    if distance_km <= 0:
        return 99.99
    raw = 100.0 * math.exp(-distance_km / 400.0)
    return round(min(99.99, max(0.01, raw)), 2)


def view_satellite(term: str) -> dict | None:
    sql = f"""
        SELECT s.Satellite_ID, s.NORAD_ID, s.Name, s.Operator, s.Status, s.Launch_Date,
               o.Inclination, o.Eccentricity, o.Apogee_km, o.Perigee_km, o.Epoch,
               {TYPE_SQL} AS Sat_Type,
               c.Band, c.Transponders,
               n.Constellation, n.Signal_Type,
               e.Sensor, e.Resolution_m,
               sci.Mission, sci.Instrument
        FROM Satellite s
        LEFT JOIN Orbit_Parameters o ON o.Satellite_ID = s.Satellite_ID
        {TYPE_JOINS}
        WHERE s.NORAD_ID = %s OR s.Name LIKE %s
        ORDER BY
            CASE
                WHEN s.NORAD_ID = %s THEN 0
                WHEN s.Name LIKE %s THEN 1
                ELSE 2
            END,
            s.Name
        LIMIT 1
    """
    like = f"%{term}%"
    prefix = f"{term}%"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (term, like, term, prefix))
        rows = _dicts(cursor)
        if not rows:
            cursor.close()
            return None
        sat = rows[0]
        cursor.execute(
            """
            SELECT Observed_At, Lat, Lon, Alt_km
            FROM Position_History
            WHERE Satellite_ID = %s
            ORDER BY Observed_At DESC
            LIMIT 5
            """,
            (sat["Satellite_ID"],),
        )
        sat["recent_positions"] = _dicts(cursor)
        cursor.execute(
            """
            SELECT Maneuver_At, Type, Notes
            FROM Maneuvers
            WHERE Satellite_ID = %s
            ORDER BY Maneuver_At DESC
            """,
            (sat["Satellite_ID"],),
        )
        sat["maneuvers"] = _dicts(cursor)
        cursor.close()
    return sat


def search_satellites(
    operator: str | None = None,
    status: str | None = None,
    sat_type: str | None = None,
) -> list[dict]:
    clauses = ["1=1"]
    params: list = []
    if operator:
        clauses.append("s.Operator LIKE %s")
        params.append(f"%{operator}%")
    if status:
        clauses.append("s.Status = %s")
        params.append(status)
    if sat_type:
        clauses.append(f"{TYPE_SQL} = %s")
        params.append(sat_type.lower())
    sql = f"""
        SELECT s.NORAD_ID, s.Name, s.Operator, s.Status,
               {TYPE_SQL} AS Sat_Type,
               o.Inclination, o.Eccentricity, o.Apogee_km, o.Perigee_km
        FROM Satellite s
        LEFT JOIN Orbit_Parameters o ON o.Satellite_ID = s.Satellite_ID
        {TYPE_JOINS}
        WHERE {' AND '.join(clauses)}
        ORDER BY s.Name
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = _dicts(cursor)
        cursor.close()
    return rows


def latest_positions() -> list[dict]:
    sql = """
        SELECT s.Satellite_ID, s.NORAD_ID, s.Name, p.Observed_At, p.Lat, p.Lon, p.Alt_km
        FROM Position_History p
        JOIN Satellite s ON s.Satellite_ID = p.Satellite_ID
        JOIN (
            SELECT Satellite_ID, MAX(Observed_At) AS Observed_At
            FROM Position_History
            GROUP BY Satellite_ID
        ) latest ON latest.Satellite_ID = p.Satellite_ID
                AND latest.Observed_At = p.Observed_At
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = _dicts(cursor)
        cursor.close()
    latest: dict[int, dict] = {}
    for row in rows:
        latest[row["Satellite_ID"]] = row
    return list(latest.values())


def generate_collision_alerts(keep: int = 8) -> list[dict]:
    """Compare latest positions, keep the closest pairs, write Collision_Alerts."""
    positions = latest_positions()
    pairs: list[tuple[float, float, dict, dict]] = []
    for i, a in enumerate(positions):
        for b in positions[i + 1 :]:
            if a["Satellite_ID"] == b["Satellite_ID"]:
                continue
            dist = _distance_km(a, b)
            # Docked modules (ISS, CSS) share a TLE and sit at ~0 km.
            if dist < 0.1:
                continue
            prob = _probability(dist)
            pairs.append((dist, prob, a, b))
    pairs.sort(key=lambda item: item[0])
    chosen = pairs[:keep]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    inserted: list[dict] = []
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE Collision_Alerts SET Status = 'expired' WHERE Status IN ('open', 'watch')"
        )
        for dist, prob, a, b in chosen:
            left, right = (a, b) if a["Satellite_ID"] < b["Satellite_ID"] else (b, a)
            status = "open" if dist < 500 else "watch"
            cursor.execute(
                """
                INSERT INTO Collision_Alerts
                    (Object_A_ID, Object_B_ID, Probability, Distance_km, Detected_At, Status)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (left["Satellite_ID"], right["Satellite_ID"], prob, round(dist, 3), now, status),
            )
            inserted.append(
                {
                    "Object_A": left["Name"],
                    "NORAD_A": left["NORAD_ID"],
                    "Object_B": right["Name"],
                    "NORAD_B": right["NORAD_ID"],
                    "Distance_km": round(dist, 3),
                    "Probability": prob,
                    "Status": status,
                    "Detected_At": now,
                }
            )
        cursor.close()
    return inserted


def list_alerts(limit: int = 20) -> list[dict]:
    sql = """
        SELECT ca.Alert_ID, ca.Probability, ca.Distance_km, ca.Detected_At, ca.Status,
               a.Name AS Object_A, a.NORAD_ID AS NORAD_A,
               b.Name AS Object_B, b.NORAD_ID AS NORAD_B
        FROM Collision_Alerts ca
        JOIN Satellite a ON a.Satellite_ID = ca.Object_A_ID
        JOIN Satellite b ON b.Satellite_ID = ca.Object_B_ID
        WHERE ca.Status IN ('open', 'watch')
        ORDER BY ca.Detected_At DESC, ca.Probability DESC
        LIMIT %s
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (limit,))
        rows = _dicts(cursor)
        cursor.close()
    return rows


def report_by_operator() -> list[dict]:
    sql = """
        SELECT COALESCE(Operator, 'Unknown') AS Operator,
               COUNT(*) AS Satellites,
               SUM(Status = 'active') AS Active
        FROM Satellite
        GROUP BY COALESCE(Operator, 'Unknown')
        ORDER BY Satellites DESC, Operator
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = _dicts(cursor)
        cursor.close()
    return rows


def report_by_type() -> list[dict]:
    sql = f"""
        SELECT {TYPE_SQL} AS Sat_Type, COUNT(*) AS Satellites
        FROM Satellite s
        {TYPE_JOINS}
        GROUP BY Sat_Type
        ORDER BY Satellites DESC
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = _dicts(cursor)
        cursor.close()
    return rows


def report_debris_trend() -> list[dict]:
    sql = """
        SELECT DATE(First_Seen) AS Day, COUNT(*) AS Debris_Count, Status
        FROM Debris_Records
        GROUP BY DATE(First_Seen), Status
        ORDER BY Day, Status
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = _dicts(cursor)
        cursor.close()
    return rows


def project_trajectory(term: str, minutes: int = 10) -> dict | None:
    """Linear extrapolation from the last two Position_History rows."""
    sat = view_satellite(term)
    if not sat:
        return None
    sql = """
        SELECT Observed_At, Lat, Lon, Alt_km
        FROM Position_History
        WHERE Satellite_ID = %s
        ORDER BY Observed_At DESC
        LIMIT 2
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (sat["Satellite_ID"],))
        points = _dicts(cursor)
        cursor.close()
    if not points:
        return {**sat, "projection": None, "reason": "no position history"}
    if len(points) == 1:
        only = points[0]
        return {
            "NORAD_ID": sat["NORAD_ID"],
            "Name": sat["Name"],
            "from": only,
            "to": only,
            "minutes": minutes,
            "projected": {
                "Lat": only["Lat"],
                "Lon": only["Lon"],
                "Alt_km": only["Alt_km"],
            },
            "note": "only one sample; projection is the last known position",
        }
    newer, older = points[0], points[1]
    dt = (newer["Observed_At"] - older["Observed_At"]).total_seconds()
    if dt <= 0:
        dt = 1.0
    scale = (minutes * 60.0) / dt
    proj_lat = float(newer["Lat"]) + (float(newer["Lat"]) - float(older["Lat"])) * scale
    proj_lon = float(newer["Lon"]) + (float(newer["Lon"]) - float(older["Lon"])) * scale
    proj_alt = float(newer["Alt_km"]) + (float(newer["Alt_km"]) - float(older["Alt_km"])) * scale
    when = newer["Observed_At"] + timedelta(minutes=minutes)
    return {
        "NORAD_ID": sat["NORAD_ID"],
        "Name": sat["Name"],
        "from": older,
        "to": newer,
        "minutes": minutes,
        "projected_at": when,
        "projected": {
            "Lat": round(proj_lat, 6),
            "Lon": round(proj_lon, 6),
            "Alt_km": round(proj_alt, 2),
        },
        "note": "linear extrapolation of last two samples — not SGP4",
    }


def sync_log(limit: int = 10) -> list[dict]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT Sync_ID, Synced_At, Source, Rows_Upserted, Status
            FROM Sync_Log
            ORDER BY Synced_At DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = _dicts(cursor)
        cursor.close()
    return rows
