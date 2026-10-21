# OIS Final Evidence for BACSE202 Report and Presentation

**Evidence date:** 2026-10-22  
**Repository inspected:** `/home/aadi/satelites-in-dbms`  
**Authoritative physical schema:** [`sql/schema.sql`](sql/schema.sql)

This document records repository facts, a read-only snapshot of the available local MariaDB database, and an audit of the project report and presentation candidates. It does not modify application code, the report, or a presentation. Database checks were read-only. No schema reset, sync, insert, update, delete, or advanced-feature installation was performed for this review.

## Evidence labels

- **Verified in source** means directly present in the files named.
- **Verified live** means observed from the local MariaDB instance on the evidence date; row counts can change after syncs.
- **Inferred** means a design interpretation based on source and schema.
- **Proposed** means a recommended correction or next step, not an existing capability.
- **Unknown / not verified** means the repository did not establish the claim or the check was not performed.

## 1. Physical schema: 11 tables

All tables in `sql/schema.sql` use `ENGINE=InnoDB`, `DEFAULT CHARSET=utf8mb4`, and `COLLATE=utf8mb4_unicode_ci`. `AUTO_INCREMENT` keys are ordinary signed `INT` columns. Indexes are B-tree indexes unless otherwise stated by MariaDB. `NULL` and defaults below reflect the SQL definitions.

### 1.1 Table dictionary

| Table | Actual columns | Keys, constraints, indexes, relationships |
|---|---|---|
| **Satellite** | `Satellite_ID INT NOT NULL AUTO_INCREMENT`; `NORAD_ID VARCHAR(16) NOT NULL`; `Name VARCHAR(120) NOT NULL`; `Operator VARCHAR(120) NULL`; `Status ENUM('active','inactive','decayed','unknown') NOT NULL DEFAULT 'active'`; `Launch_Date DATE NULL` | PK `Satellite_ID`; unique key `uq_satellite_norad(NORAD_ID)`; secondary indexes `idx_satellite_name(Name)` and `idx_satellite_operator(Operator)`. Parent for orbit, positions, maneuvers, both alert object references, optional debris parent, and all four subtype tables. A satellite can be deleted; most dependents cascade. |
| **Orbit_Parameters** | `Satellite_ID INT NOT NULL`; `Inclination DECIMAL(8,4) NOT NULL`; `Eccentricity DECIMAL(12,10) NOT NULL`; `Apogee_km DECIMAL(10,2) NOT NULL`; `Perigee_km DECIMAL(10,2) NOT NULL`; `Epoch DATETIME NOT NULL` | PK `Satellite_ID`, also FK `fk_orbit_satellite` → `Satellite(Satellite_ID) ON DELETE CASCADE`. No additional explicit index. At most one orbit row per satellite; zero rows are allowed. |
| **Position_History** | `Position_ID INT NOT NULL AUTO_INCREMENT`; `Satellite_ID INT NOT NULL`; `Observed_At DATETIME NOT NULL`; `Lat DECIMAL(9,6) NOT NULL`; `Lon DECIMAL(9,6) NOT NULL`; `Alt_km DECIMAL(10,2) NOT NULL` | PK `Position_ID`; composite index `idx_position_sat_time(Satellite_ID, Observed_At)`; FK `fk_position_satellite` → `Satellite(Satellite_ID) ON DELETE CASCADE`. One satellite can have many samples. There is no unique constraint on `(Satellite_ID, Observed_At)`. |
| **Maneuvers** | `Maneuver_ID INT NOT NULL AUTO_INCREMENT`; `Satellite_ID INT NOT NULL`; `Maneuver_At DATETIME NOT NULL`; `Type VARCHAR(60) NOT NULL`; `Notes VARCHAR(255) NULL` | PK `Maneuver_ID`; index `idx_maneuver_sat(Satellite_ID)`; FK `fk_maneuver_satellite` → `Satellite(Satellite_ID) ON DELETE CASCADE`. One satellite can have many maneuver records. |
| **Collision_Alerts** | `Alert_ID INT NOT NULL AUTO_INCREMENT`; `Object_A_ID INT NOT NULL`; `Object_B_ID INT NOT NULL`; `Probability DECIMAL(5,2) NOT NULL`; `Distance_km DECIMAL(12,3) NOT NULL`; `Detected_At DATETIME NOT NULL`; `Status ENUM('open','watch','expired','cleared') NOT NULL DEFAULT 'open'` | PK `Alert_ID`; indexes `idx_alert_pair(Object_A_ID,Object_B_ID)` and `idx_alert_status(Status,Detected_At)`; FKs `fk_alert_object_a` and `fk_alert_object_b`, both to `Satellite(Satellite_ID) ON DELETE CASCADE`; checks `chk_alert_distinct(Object_A_ID <> Object_B_ID)` and `chk_alert_probability(Probability >= 0 AND Probability <= 100)`. Each alert references two satellites; neither pair nor timestamp is unique. |
| **Debris_Records** | `Debris_ID INT NOT NULL AUTO_INCREMENT`; `Parent_Satellite_ID INT NULL`; `Catalog_ID VARCHAR(16) NOT NULL`; `First_Seen DATETIME NOT NULL`; `Status ENUM('tracked','decayed','unknown') NOT NULL DEFAULT 'tracked'` | PK `Debris_ID`; unique key `uq_debris_catalog(Catalog_ID)`; index `idx_debris_parent(Parent_Satellite_ID)`; FK `fk_debris_parent` → `Satellite(Satellite_ID) ON DELETE SET NULL`. Parent is optional and cleared if its satellite is deleted. |
| **Comm_Satellite** | `Satellite_ID INT NOT NULL`; `Band VARCHAR(40) NOT NULL`; `Transponders INT NULL` | PK `Satellite_ID`; FK `fk_comm_satellite` → `Satellite(Satellite_ID) ON DELETE CASCADE`. Subtype row is optional per satellite. |
| **Nav_Satellite** | `Satellite_ID INT NOT NULL`; `Constellation VARCHAR(40) NOT NULL`; `Signal_Type VARCHAR(40) NOT NULL` | PK `Satellite_ID`; FK `fk_nav_satellite` → `Satellite(Satellite_ID) ON DELETE CASCADE`. |
| **EO_Satellite** | `Satellite_ID INT NOT NULL`; `Sensor VARCHAR(60) NOT NULL`; `Resolution_m DECIMAL(8,2) NULL` | PK `Satellite_ID`; FK `fk_eo_satellite` → `Satellite(Satellite_ID) ON DELETE CASCADE`. |
| **Sci_Satellite** | `Satellite_ID INT NOT NULL`; `Mission VARCHAR(80) NOT NULL`; `Instrument VARCHAR(80) NOT NULL` | PK `Satellite_ID`; FK `fk_sci_satellite` → `Satellite(Satellite_ID) ON DELETE CASCADE`. |
| **Sync_Log** | `Sync_ID INT NOT NULL AUTO_INCREMENT`; `Synced_At DATETIME NOT NULL`; `Source VARCHAR(80) NOT NULL`; `Rows_Upserted INT NOT NULL DEFAULT 0`; `Status ENUM('success','partial','failed') NOT NULL` | PK `Sync_ID`; index `idx_sync_time(Synced_At)`. No foreign keys; a sync run is not relationally tied to rows changed by that run. |

The schema script first disables FK checks, drops these tables, and then recreates them. Consequently, `python -m app --init-db` is a **destructive reset**, not a harmless “ensure schema exists” operation: `app/db.py:init_schema()` runs `sql/schema.sql` and then `sql/seed.sql`. Do not run it against a database whose contents must be preserved.

### 1.2 Mermaid ER diagram

This diagram matches the SQL foreign keys. Subtype exclusivity is an intended business rule only; it is not enforced by these FKs.

```mermaid
erDiagram
    Satellite ||--o| Orbit_Parameters : "has current parameters"
    Satellite ||--o{ Position_History : "has samples"
    Satellite ||--o{ Maneuvers : "has maneuver records"
    Satellite ||--o{ Collision_Alerts : "Object A"
    Satellite ||--o{ Collision_Alerts : "Object B"
    Satellite ||--o{ Debris_Records : "optional parent"
    Satellite ||--o| Comm_Satellite : "optional subtype"
    Satellite ||--o| Nav_Satellite : "optional subtype"
    Satellite ||--o| EO_Satellite : "optional subtype"
    Satellite ||--o| Sci_Satellite : "optional subtype"

    Satellite {
        int Satellite_ID PK
        varchar NORAD_ID UK
        varchar Name
        varchar Operator
        enum Status
        date Launch_Date
    }
    Orbit_Parameters {
        int Satellite_ID PK_FK
        decimal Inclination
        decimal Eccentricity
        decimal Apogee_km
        decimal Perigee_km
        datetime Epoch
    }
    Position_History {
        int Position_ID PK
        int Satellite_ID FK
        datetime Observed_At
        decimal Lat
        decimal Lon
        decimal Alt_km
    }
    Maneuvers {
        int Maneuver_ID PK
        int Satellite_ID FK
        datetime Maneuver_At
        varchar Type
        varchar Notes
    }
    Collision_Alerts {
        int Alert_ID PK
        int Object_A_ID FK
        int Object_B_ID FK
        decimal Probability
        decimal Distance_km
        datetime Detected_At
        enum Status
    }
    Debris_Records {
        int Debris_ID PK
        int Parent_Satellite_ID FK
        varchar Catalog_ID UK
        datetime First_Seen
        enum Status
    }
    Comm_Satellite {
        int Satellite_ID PK_FK
        varchar Band
        int Transponders
    }
    Nav_Satellite {
        int Satellite_ID PK_FK
        varchar Constellation
        varchar Signal_Type
    }
    EO_Satellite {
        int Satellite_ID PK_FK
        varchar Sensor
        decimal Resolution_m
    }
    Sci_Satellite {
        int Satellite_ID PK_FK
        varchar Mission
        varchar Instrument
    }
    Sync_Log {
        int Sync_ID PK
        datetime Synced_At
        varchar Source
        int Rows_Upserted
        enum Status
    }
```

`Sync_Log` is intentionally unconnected in this physical schema: there is no FK from it to `Satellite` or other tables.

### 1.3 Functional dependencies and normal forms

The functional dependencies below use the keys actually declared in `sql/schema.sql`. They assume identifiers are stable and that the unique constraints are maintained.

- `Satellite_ID → NORAD_ID, Name, Operator, Status, Launch_Date`.
- Because `NORAD_ID` is `UNIQUE NOT NULL`, it is an alternate candidate key: `NORAD_ID → Satellite_ID, Name, Operator, Status, Launch_Date`.
- `Orbit_Parameters.Satellite_ID → Inclination, Eccentricity, Apogee_km, Perigee_km, Epoch`.
- `Position_ID → Satellite_ID, Observed_At, Lat, Lon, Alt_km`.
- `Maneuver_ID → Satellite_ID, Maneuver_At, Type, Notes`.
- `Alert_ID → Object_A_ID, Object_B_ID, Probability, Distance_km, Detected_At, Status`.
- `Debris_ID → Parent_Satellite_ID, Catalog_ID, First_Seen, Status`; its required unique alternate key also gives `Catalog_ID → Debris_ID, Parent_Satellite_ID, First_Seen, Status`.
- In each subtype table, `Satellite_ID →` all of that subtype’s attributes.
- `Sync_ID → Synced_At, Source, Rows_Upserted, Status`.

**1NF — atomic values:** each defined column stores one value of its declared type. Repeated position samples, maneuvers, alerts, and subtype details are in separate rows/tables, not repeating column groups. The SQL does not enforce coordinate ranges, eccentricity bounds, or a universal date/time policy; atomicity does not imply domain validity.

**2NF — no partial dependency:** every table’s declared primary key has one column. Thus no non-key attribute can depend on only part of a composite primary key. No table uses a composite primary key. This is a structural argument; it assumes the listed candidate keys reflect the business rules.

**3NF — no non-key determinant:** under the declared dependencies above, each non-key attribute depends on a key, and no non-key attribute is modeled as determining another non-key attribute. Satellite identity, orbit state, time-series samples, maneuvers, alerts, debris, and type-specific attributes are separated. `Operator` remains text on `Satellite`; there is no operator entity with independently stored facts. Under the stated assumptions, the decomposition is consistent with 3NF and also BCNF. This is not an exhaustive proof of every real-world dependency: source and domain rules would need confirmation.

**Assumptions and schema limitations:**

- The 1:1 orbit relationship is **zero-or-one** from a satellite to an orbit row in the physical schema, not mandatory participation. `Orbit_Parameters.Satellite_ID` as PK prevents a second orbit row; no constraint requires a first one.
- The four subtype tables are intended as disjoint specializations, but separate FKs do not stop the same `Satellite_ID` appearing in multiple subtype tables. There is also no total-specialization constraint requiring every satellite to have a subtype.
- `Collision_Alerts` permits repeated pairs and repeated timestamps; it does not canonicalize Object A/Object B order. Its checks only prevent self-alerts and bound probability.
- `Position_History` permits duplicate satellite/time samples. The application’s latest-position query can return ties and then folds rows by satellite in Python.
- `Orbit_Parameters` has no checks in `schema.sql` for valid inclination, eccentricity, or `Apogee_km >= Perigee_km`. A trigger for apogee/perigee exists in the optional advanced SQL, but it is insert-only and is not in the base schema.
- The base schema’s check constraints are only the two on collision alerts. No launch-events entity or operator table exists.

## 2. Application features and source map

The operational code path is the top-level `app` and `ingest` packages. `src/satelites_in_dbms/__init__.py` is a packaging stub that prints a greeting; the `pyproject.toml` console entry point targets that stub, while the setup launchers use `python -m app`.

| Feature | Verified implementation and entry points |
|---|---|
| Application CLI/TUI selection | `app/cli.py:main()` parses `--init-db`, `--sync`, `--live`, `--alerts`, `--report`, `--view`, `--art`, `--tui`, and `--cli`. Default behavior imports and runs `app.tui`. `--art` bypasses the DB; most other modes call `ping()`. |
| Legacy text menu | `app/cli.py:loop()`, `view_flow()`, `search_flow()`, `alerts_flow()`, `sync_flow()`, `report_flow()`, and `art_flow()`. |
| Database connection and SQL runner | `app/db.py:settings()`, `connect()`, `get_connection()`, `ping()`, `init_schema()`, `execute_raw_sql()`. `get_connection()` commits on normal exit and rolls back on a Python exception. |
| Satellite lookup/detail | `app/queries.py:view_satellite()` finds by NORAD ID or name, joins orbit and subtype data, then fetches recent positions and maneuvers. Rendered by `app/ui.py:print_telemetry_card()` or the TUI detail panel. |
| Fleet search/filter | `app/queries.py:search_satellites()` filters optional operator, status, and subtype. TUI uses `OISApp.load_catalog()` and `apply_catalog_filters()` in `app/tui.py`. |
| Alerts | `app/queries.py:latest_positions()`, `generate_collision_alerts()`, and `list_alerts()`. Alerts compare latest stored positions with an ECEF distance calculation and a heuristic probability function; this is not validated conjunction analysis. |
| Reports | `report_by_operator()`, `report_by_type()`, `report_debris_trend()`, `sync_log()` in `app/queries.py`; trajectory projection in `project_trajectory()`. CLI options are `operator`, `type`, `debris`, `sync`. The legacy menu also exposes trajectory. |
| TUI screens | `app/tui.py:OISApp.compose()` defines home, dashboard/catalog, analysis, SQL shell, and settings areas. `switch_view()`, `load_catalog()`, `load_alerts()`, `load_analysis_report()`, `load_report_subview()`, `execute_shell_query()`, and `save_settings_panel()` implement the main interactions. |
| TUI analysis scans | `app/tui.py:run_analysis_scan()` supports conjunction, low-perigee, eccentricity, and data-gap scans. These are screening rules over available rows, not operational tracking. |
| TUI SQL shell/database explorer | `app/tui.py:load_database_structure()` reads MariaDB metadata; `execute_shell_query()` delegates to `app.db:execute_raw_sql()`. Quick chips issue `SHOW TABLES` and SELECTs over satellite, alert, position, and debris tables. The shell accepts arbitrary SQL allowed by the configured database account. |
| User-confirmed catalog deletion | `app/tui.py:delete_displayed_satellites()` deletes displayed satellite rows by NORAD ID after a typed confirmation. FK cascades can remove associated records. |
| Settings | `app/config.py:load_config()` and `save_config()` load defaults, `.env`, and ignored `config.json`; TUI `save_settings_panel()` writes DB and API settings. Stored locally, not encrypted by this code. |
| Satellite ASCII art | `app/art.py` and CLI `--art [--art-style clean|stars|raw]`; no DB needed. |
| Packaging/setup | `setup.sh`, `setup.ps1`, `ois`, and `ois.cmd`; `docker-compose.yml` launches local MariaDB 11. `pyproject.toml` requires Python `>=3.14`; `requirements.txt` supplies runtime package pins. |

### 2.1 SQL used by the application

This inventory classifies query patterns present in `app/`, `ingest/`, and CLI/TUI actions, not every possible SQL statement a user could type into the SQL shell.

#### CRUD and DDL

- **Create (DDL):** `app/db.py:init_schema()` runs every statement in `sql/schema.sql` then `sql/seed.sql`; schema SQL creates 11 tables, indexes, FKs, and checks after dropping existing tables.
- **Create/upsert (DML):** `ingest/celestrak.py:sync()` inserts/upserts `Satellite`, `Orbit_Parameters`, subtype rows, `Position_History`, `Debris_Records`, and `Sync_Log`. `ingest/apis.py:run_multi_source_sync()` performs similar catalog/orbit/subtype upserts, may insert an ISS position and NASA NEO rows into `Debris_Records`, and logs syncs. `app/queries.py:generate_collision_alerts()` inserts alert rows.
- **Read (DML):** `app/queries.py` functions listed above use SELECTs for views, searches, latest positions, alerts, reports, trajectory, and sync history. `app/db.py:ping()` issues `SELECT 1`. TUI metadata and quick-chip queries are in `app/tui.py`.
- **Update (DML):** ingestion uses `INSERT ... ON DUPLICATE KEY UPDATE` for satellites, current orbit parameters, subtype records in the multi-source path, and debris. `generate_collision_alerts()` first runs `UPDATE Collision_Alerts SET Status='expired' WHERE Status IN ('open','watch')` before inserting a new scan. There is no dedicated application feature for editing arbitrary satellite fields.
- **Delete (DML):** `app/tui.py:delete_displayed_satellites()` removes selected displayed satellite records; `ingest/celestrak.py:_upsert_type()` deletes existing subtype rows before inserting the current subtype. FK cascades are configured in `schema.sql`. The generic SQL shell also accepts DELETE if the shared DB account permits it.

#### Joins

- `view_satellite()` and `search_satellites()` left-join `Satellite` to `Orbit_Parameters` and all four subtype tables using `TYPE_JOINS` in `app/queries.py`.
- `latest_positions()` joins `Position_History` to `Satellite` and to a derived latest-time result.
- `list_alerts()` joins `Collision_Alerts` to `Satellite` twice, once for each object.
- `report_by_type()` left-joins the four subtype tables.
- `app/tui.py:run_analysis_scan()` joins satellites and orbit parameters, plus a latest-position derived table for data-gap/conjunction screening.
- Optional advanced view/procedure SQL also joins these tables; see section 4.

#### Subqueries

- `app/queries.py:latest_positions()` uses a derived-table subquery selecting `Satellite_ID, MAX(Observed_At)` grouped by satellite, then joins it back to history.
- `app/tui.py:run_analysis_scan()` uses a similar derived latest-position query.
- No correlated `EXISTS`/`IN (SELECT ...)` query or nested scalar subquery was found in normal application SQL. `IN (...)` appears in the parameterized TUI DELETE list but that is a literal placeholder list, not a subquery.

#### Aggregates

- `report_by_operator()`: `COUNT(*)` and `SUM(Status = 'active')`, grouped by operator.
- `report_by_type()`: `COUNT(*)`, grouped by derived satellite type.
- `report_debris_trend()`: `COUNT(*)`, grouped by date and debris status.
- `latest_positions()` and the TUI scan query: `MAX(Observed_At)` grouped by satellite.
- `advanced_features.sql:CountAlertsForSatellite`: `COUNT(Alert_ID)`.
- No application window functions, `HAVING`, `AVG`, or general-purpose analytical aggregation with windowing was found.

#### Transactions

- `app/db.py:get_connection()` starts the connector’s normal transaction behavior (`autocommit=False`), commits on successful context exit, and rolls back on exceptions. Multi-statement syncs and the alert-expire/insert workflow are grouped inside this context manager.
- The application does **not** issue explicit SQL `START TRANSACTION`, `COMMIT`, `ROLLBACK`, or `SAVEPOINT` statements in its normal functions. A context is transaction-like but is not evidence that SAVEPOINT workflows were implemented/tested.
- `execute_raw_sql()` opens and closes a connection for each shell submission; successful statements are committed when its context exits. Separate shell submissions do not share one open transaction, so a multi-command rollback demo should use one SQL-client connection rather than issuing each command separately through this shell.
- DDL in `schema.sql` implicitly commits in MariaDB and is destructive; do not present `--init-db` as a rollback-safe transaction demonstration.

## 3. Advanced SQL objects

`sql/advanced_features.sql` contains the objects below. It is separate from the base schema and from `sql/seed.sql`.

| Kind | SQL object | Purpose / limitation |
|---|---|---|
| View | `active_satellites_view` | Inner-joins active `Satellite` rows to `Orbit_Parameters`, projecting selected satellite fields and apogee/perigee/inclination. Active satellites with no orbit row are excluded. |
| View | `high_risk_conjunctions_view` | Joins alerts to both satellite identities and filters `Probability >= 95`; it does not filter on alert status. |
| Procedure | `GetActiveSatellites()` | Returns active satellite catalog rows ordered by name. |
| Procedure | `GetSatellitesByOperator(op_name)` | Substring-matches `Operator`, left-joins orbit parameters, orders by satellite name. |
| Procedure | `CountAlertsForSatellite(sat_id)` | Counts alerts where the satellite is either Object A or B. |
| Procedure | `DisplaySatelliteInventoryCursor()` | Uses an explicit cursor inside a stored procedure to select at most five satellite rows. No `ORDER BY` is specified, so which five are returned is not guaranteed. |
| Function | `ClassifyOrbitRegime(apogee, perigee)` | Computes mean altitude and labels LEO/MEO/GEO/HEO using hard-coded thresholds. It is an illustrative classifier, not an orbital determination model. |
| Function | `EvaluateRiskCategory(prob)` | Maps percentage cutoffs to CRITICAL/HIGH/MODERATE/LOW. |
| Trigger | `check_orbit_apogee_perigee` | Before INSERT on `Orbit_Parameters`, rejects apogee below perigee. It does not cover UPDATE. |
| Trigger | `auto_classify_conjunction_status` | Before INSERT on `Collision_Alerts`, overrides status to `open` for distance below 5 km and `watch` otherwise. It does not cover UPDATE and may override a caller’s status. |

The file header mentions transactions, but the file contains no transaction demonstration or savepoint statements. Its cursor is part of `DisplaySatelliteInventoryCursor`; it is not a separate standalone SQL object. The normal installers do **not** install this file: `setup.sh` and `setup.ps1` call `python -m app --init-db`, whose `init_schema()` applies only `schema.sql` and `seed.sql`. Advanced SQL must be applied separately by an operator with suitable privileges. No installation was performed for this evidence.

## 4. Local database inspection (read-only)

**Verified live on 2026-10-22:** the configured local database connection worked when queried outside the restricted sandbox. Only metadata and `COUNT(*)` values were read; no credentials or record contents were printed.

- Server: `11.8.9-MariaDB-ubu2404` (MariaDB binary distribution); default storage engine `InnoDB`.
- `SHOW FULL TABLES`: 11 base tables and 2 views: `active_satellites_view`, `high_risk_conjunctions_view`.
- Installed routines: functions `ClassifyOrbitRegime`, `EvaluateRiskCategory`; procedures `CountAlertsForSatellite`, `DisplaySatelliteInventoryCursor`, `GetActiveSatellites`, `GetSatellitesByOperator`.
- Installed triggers: **0**.
- Live table row counts at check time:

| Table | Rows |
|---|---:|
| Satellite | 2,855 |
| Orbit_Parameters | 2,855 |
| Position_History | 23 |
| Maneuvers | 1 |
| Collision_Alerts | 57 |
| Debris_Records | 19 |
| Comm_Satellite | 1 |
| Nav_Satellite | 1 |
| EO_Satellite | 9 |
| Sci_Satellite | 2,845 |
| Sync_Log | 5 |

- Live indexes observed by `SHOW INDEX`: table-level PKs; `Satellite` also has `uq_satellite_norad`, `idx_satellite_name`, `idx_satellite_operator`; `Position_History` has `idx_position_sat_time`; `Maneuvers` has `idx_maneuver_sat`; `Collision_Alerts` has `idx_alert_pair`, `idx_alert_status`, and an InnoDB supporting index on `Object_B_ID`; `Debris_Records` has `uq_debris_catalog`, `idx_debris_parent`; each subtype has its PK; `Sync_Log` has `idx_sync_time`. The composite indexes expose one row per indexed column in `SHOW INDEX` output.

This is a mutable local snapshot, not a promise of the seed-only state or a portable benchmark dataset. The larger satellite/subtype/alert counts indicate local sync/demo activity beyond the small `sql/seed.sql` fixture. The read-only connection used project configuration internally; credentials are intentionally omitted.

## 5. Tests and verification

The checked-in `tests/test_tui.py` has three test methods:

1. `TestOISMinimalistTUI.test_home_page_and_navigation` exercises home/dashboard/shell/settings/help interactions. It clicks Save Settings, which can write `config.json` and `.env` via `app/config.py:save_config()`; it was not run.
2. `TestOISMinimalistTUI.test_raw_sql_execution` executes only `SELECT COUNT(*) FROM Satellite`, a read-only DB query.
3. `TestOISMinimalistTUI.test_offline_multi_api_sync_and_progress` calls `run_multi_source_sync(source="cache", live=False, ...)`; despite “offline” referring to network source selection, the function proceeds to upsert rows and log syncs in MariaDB. It was not run to preserve database state.

**Safe test executed:**

```text
.venv/bin/python -m unittest discover -s tests -p test_tui.py -k test_raw_sql_execution
Ran 1 test in 0.021s
OK
```

The first attempt inside the sandbox failed because it could not create a TCP/IP socket to the local DB; the exact command above was rerun with approved local database access and passed. The full test suite was not run because two tests have side effects (database writes and settings-file writes). No other test result is claimed.

## 6. Performance evidence

A small, read-only timing was safe on the local instance. This is a reproducible sample for one existing report query, not a broad performance claim.

- Server: `11.8.9-MariaDB-ubu2404`; default engine InnoDB.
- Dataset: 2,855 `Satellite` rows (same local snapshot as section 4).
- Query: exact aggregation pattern in `app/queries.py:report_by_operator()`:

```sql
SELECT COALESCE(Operator, 'Unknown') AS Operator,
       COUNT(*) AS Satellites,
       SUM(Status = 'active') AS Active
FROM Satellite
GROUP BY COALESCE(Operator, 'Unknown')
ORDER BY Satellites DESC, Operator;
```

- `EXPLAIN` row: `id=1`, `select_type=SIMPLE`, `table=Satellite`, `type=ALL`, `possible_keys=NULL`, `key=NULL`, estimated `rows=2839`, `Extra=Using temporary; Using filesort`.
- Method: one warm-up, then 20 executions on a warm local client/server connection. Python `time.perf_counter_ns()` measured from immediately before `cursor.execute(query)` through `fetchall()`, converted to milliseconds. This includes connector round-trip and result fetching, excludes connection establishment and setup.
- Result: median **2.246 ms**, minimum **1.863 ms**, maximum **2.747 ms**. Trial count 20. No p95 was calculated. Cache state, competing workload, CPU/storage configuration, and cold-start performance were not controlled. Do not generalize this to other queries or environments.

## 7. Ingestion sources and cache behavior

### CLI sync path (`ingest/celestrak.py`)

- `fetch_live()` requests CelesTrak GP JSON from `https://celestrak.org/NORAD/elements/gp.php` for stations, GPS ops, Iridium NEXT, Planet, science, and a small Iridium debris group.
- It writes local `cache/celestrak.json` and `cache/debris.json`. `load_catalog(live)` uses cached files when present and `live=False`; if the catalog cache is absent, it fetches live even when `live=False`. CLI `--sync` defaults to `live=False`; `--sync --live` forces a live fetch.
- `sync()` derives approximate orbital elements and approximate lat/lon/alt from GP fields; code comments state its conversion is not SGP4. It upserts satellite/orbit data, inserts position history, sets one satellite subtype, inserts debris, and logs the sync. This is not scheduled ingestion; no scheduler/cron/event job exists in the checked-in setup.

### Multi-source TUI/legacy menu path (`ingest/apis.py`)

- Live-capable public endpoints: CelesTrak GP JSON, SatNOGS satellite catalog, WhereTheISS ISS position, and NASA NeoWs daily Near-Earth Objects.
- Optional credentialed sources: N2YO requires an API key; Space-Track requires account identity/password. Those settings are stored through `app/config.py`; no credentials are present in this evidence file.
- Local cache behavior: CelesTrak cache in `cache/celestrak.json` can be reused if less than two hours old and validated by simple shape/size checks. Space-Track cache can be reused if less than one hour old. Selecting `cache` / non-live mode falls back to local CelesTrak JSON. Multi-source functions still write to MariaDB when a cache is used.
- NASA NeoWs returns near-Earth asteroid records; the current ingestion maps them into `Debris_Records`. This model conflates near-Earth asteroids with orbital debris and should be described as demo data mapping, not an authoritative debris catalog.
- WhereTheISS is an observed position feed for the ISS; other satellite position rows from `ingest/celestrak.py` are approximations, not live GPS observations.
- `Launch Library 2`, ESA DISCOS, CDM feeds, and SOCRATES ingestion are not present in these source files. All source findings above are based on `ingest/apis.py` and `ingest/celestrak.py` URLs and functions.

## 8. Authentication, roles, deployment, and security

- **Application users/authentication:** none found. The CLI/TUI has no login, session, or per-user identity model.
- **Database authentication:** the app connects using one configured MariaDB account from `.env`/`config.json` or defaults in `app/db.py:settings()`. `docker-compose.yml` provisions a local database account. The app does not provision least-privilege roles or per-user grants.
- **Authorization:** no `CREATE USER`, `GRANT`, role definitions, or application permission checks found in the inspected SQL/Python. The TUI’s `DELETE` typed confirmation is an accidental-action confirmation, not authorization. The SQL shell can execute arbitrary statements allowed to the shared DB account.
- **Secret handling:** `.env` and `config.json` are ignored by `.gitignore`, but `app/config.py:save_config()` writes API and DB settings as local plaintext. Do not commit those files or share them. Compose contains development DB credentials in the repository configuration; replace defaults and restrict the port/account before any shared or remote deployment.
- **Transport:** API source URLs use HTTPS. No MariaDB TLS/SSL client option is configured in `app/db.py:connect()`; no TLS requirement for database traffic was verified.
- **Deployment:** `docker-compose.yml` maps MariaDB to a host port and stores data in a named local Docker volume. This is local containerized development/demo deployment, not cloud deployment. No cloud host, managed database, production backup/restore, HA setup, or network policy is configured.
- **Security claims:** describe the repository as a local prototype with DB-level account authentication only. Do not claim application authentication, RBAC, encrypted secret storage, cloud security, or production security.

## 9. Existing report and presentation discrepancies

The repository contains [`docs/DBMS_DA2_REPORT.md`](docs/DBMS_DA2_REPORT.md). Related external files found and inspected without modification:

- `/home/aadi/BACSE202_1.docx` — BACSE202 report candidate.
- `/home/aadi/Downloads/OIS_Project_Presentation.pptx` — OIS project deck candidate.
- `/home/aadi/Downloads/Orbital Intelligence System Overview.pptx` — older overview deck with additional outdated claims.

The docx contains institution/submission metadata that is not reproduced here. Correct its administrative placeholders separately before submission; in particular, the certificate paragraph still has `[STUDENT NAME] [REGISTRATION NUMBER]`. The assessment below concerns technical content.

### High-priority technical corrections

1. **Entity/table count and names:** the report abstract and body describe seven core entities, `Launch_Events`, and elsewhere eight tables; the physical SQL has 11 tables. It has `Sync_Log` and four subtype tables; it has no `Launch_Events` table. Replace diagrams/data dictionaries from `sql/schema.sql`.
2. **Keys/cardinalities:** Orbit parameters are PK/FK `Satellite_ID` and therefore 0..1 per satellite, not 1:N. Position and maneuver tables are 1:N. Alerts have two satellite FKs. The report’s count of 10 FK relationships matches the base schema’s 10 FKs; its claim of 15 check constraints does not—the schema declares two CHECK constraints. Optional advanced SQL adds two triggers, not 13 extra checks.
3. **Data sources:** report/PPT claims for Launch Library 2, ESA DISCOS ingestion, CDM/SOCRATES ingestion, and scheduled four-hour loading are not implemented. Current code sources are described in section 7. CelesTrak GP JSON is not equivalent to ingesting CDMs or SOCRATES results.
4. **Application technology:** report text mentions Python 3.12, SQLAlchemy, and MariaDB/SQLite local demo. The current `pyproject.toml` requires Python 3.14+, `app/db.py` uses `mysql-connector-python` directly (no SQLAlchemy), and Docker Compose runs MariaDB 11. The project is not an SQLite app.
5. **Normalization:** report’s “seven tables” proof and statements about `orbit_param_id` do not match current keys/schema. The SQL uses single-column PKs, `NORAD_ID` and `Catalog_ID` alternate unique keys, and the FDs in section 1.3. A 3NF/BCNF explanation must be tied to those actual dependencies and stated assumptions; current SQL does not prove source/domain dependencies beyond declared constraints.
6. **Unsupported SQL feature counts:** the report claims six views, six stored procedures, six functions, six triggers, three cursors, all join types, nested subqueries, windowing, and transaction/savepoint validation. `sql/advanced_features.sql` defines two views, four procedures (one contains a cursor), two functions, and two triggers. Application SQL has joins, one derived-table aggregate pattern, and basic aggregates; no window functions, generalized nested scalar/correlated subqueries, or explicit savepoint logic. The live database had two views, four procedures, two functions, **zero triggers**.
7. **CRUD/test claims:** full generic CRUD and 108/108 passing tests are not supported by `tests/test_tui.py`, which contains only three test methods. One read-only query test was run successfully for this evidence. The cache sync test writes to the DB; full suite not run. There are no verified logs for the report’s 108 claimed tests, CRUD counts, integrity counts, trigger tests, or transaction tests.
8. **Performance:** the report lists sub-millisecond and low-millisecond thresholds and a window ranking workload without reproducible index plans/logs; the local query plan here did a table scan/temp sort. Only the query and 20-trial method in section 6 are verified. Replace report numbers with that limited qualified result or remove benchmark claims.
9. **Sample data:** `sql/seed.sql` seeds five satellites and small demo rows; the live DB snapshot had 2,855 satellites and 2,845 science subtype rows after additional local activity. Report examples of Hubble/Starlink/other object counts should not be presented as seed contents unless separately verified in this live dataset and attributed to a source.
10. **Security/roles:** report and slide role matrices are recommendations, not deployed authorization. No application login/RBAC or SQL grant scripts exist. Label roles as proposed, not current.
11. **Dashboard/deployment:** current interface is a terminal TUI and CLI, not a web/research dashboard or cloud deployment. Some slides correctly mark dashboard/cloud as proposed; keep those caveats. Docker local hosting is not cloud hosting.
12. **Physical integrity:** base schema has just two alert checks. Orbit validation trigger exists only in optional `advanced_features.sql` and was not installed in the live DB; it only checks INSERT. Do not claim all orbit domain constraints are enforced.
13. **Collision semantics:** `generate_collision_alerts()` uses approximate latest positions and a heuristic exponential probability (`app/queries.py:_probability()`), not trusted Pc/CDM computation or validated collision prediction. TLE conversion path also says it is approximate and not SGP4.
14. **Sync_Log provenance:** sync log stores timestamp/source/count/status only; it does not link changed rows or persist raw source provenance per satellite/measurement. Slide quality-gate statements about retained provenance overstate schema support.
15. **Test and screenshot placeholders:** the docx includes `[INSERT ALL TABLES]`, `[Insert all tables]`, `[INSERT SCREENSHOTS]`, and test/performance output blocks described as drafts/not independently verified. Slide 24 instructs the presenter to insert screenshots; slide 10 is only an EER title, and slides 13–16 are normalization headings without extracted explanatory content. Replace with real output or remove.

### Deck-specific notes

- `OIS_Project_Presentation.pptx` is comparatively cautious: slides 18, 21, 23–25 label cloud/dashboard/benchmarks as unverified or proposed. Preserve those caveats. Correct slide 6’s Launch Library 2 reference; slide 7’s provenance gate is not fully represented in schema; slide 9’s ER core omits the four subtype tables and makes the orbital relationship ambiguous; slide 11’s own note to reconcile against SQL should be addressed. Slide 17 describes transaction/savepoint evidence not present in normal app flow. Slide 20 correctly says roles need verification, but these roles are proposed only.
- `Orbital Intelligence System Overview.pptx` is less current: it says Orbit Parameters is 1:N, lists Launch Library 2 and “scheduled updates,” and describes a research dashboard workflow. Those are inaccurate for current schema/code. Mark interactive map, prediction dashboard, AI anomaly detection, and space weather as future proposals only.
- `docs/DBMS_DA2_REPORT.md` also contains extensive conceptual SQL/data outputs not equivalent to executed repository operations. Treat examples and outputs as illustrative unless a reproducible run is separately recorded.

## 10. Practical live demo sequence

Use the existing local DB and avoid `--init-db` during the demo: it drops/recreates tables. Choose a database state prepared for demonstration and do not run sync if its writes are unwanted.

1. **Show the running interface:** from the repo, run `./ois` (or `.venv/bin/python -m app`). Show home, dashboard/catalog, analysis, SQL shell, and settings; identify that this is a terminal interface.
2. **Record retrieval:** use the catalog search for `25544` or run `./ois --view 25544`. In SQL Shell, show `SELECT NORAD_ID, Name, Operator, Status FROM Satellite WHERE NORAD_ID='25544';`.
3. **Join:** run `SELECT s.NORAD_ID, s.Name, o.Inclination, o.Apogee_km, o.Perigee_km FROM Satellite s JOIN Orbit_Parameters o ON o.Satellite_ID=s.Satellite_ID WHERE s.NORAD_ID='25544';`.
4. **Aggregate:** use the TUI Operator or Type report, or CLI `./ois --report operator`; point to `COUNT(*)` / active `SUM()` in `app/queries.py:report_by_operator()`.
5. **Insert, update, and delete safely:** use one disposable test satellite with a new, unique NORAD string (for example `DEMO20261022X`, after checking it is unused). Through the SQL shell issue one statement at a time: `INSERT` the marker row, `SELECT` it, `UPDATE` its status, `SELECT` again, then `DELETE` that same marker and verify it is gone. `execute_raw_sql()` commits each successful submission. Do not use an existing record; the delete confirmation in the UI can delete many currently displayed rows and cascades to dependents.
6. **Subquery / alerts:** demonstrate the latest-position pattern in `app/queries.py:latest_positions()` or use the Alerts page. Explain clearly that the probability is a heuristic; recalculating alerts writes/updates `Collision_Alerts`.
7. **Advanced features available live:** run `SELECT * FROM active_satellites_view LIMIT 5;`, `CALL GetActiveSatellites();`, and `SELECT EvaluateRiskCategory(95.00);`. The live check found these views/procedures/functions. State that triggers are defined in source but none are installed in the checked local database; do not claim a trigger fired.
8. **Ingestion/cache only if desired:** show cached JSON vs live fetch distinction. A sync writes data and may create alerts, so treat it as a mutating demo. Avoid launching `--init-db`.
9. **Conclude with installed object evidence:** use the TUI database structure explorer or read-only `SHOW FULL TABLES`, `SHOW INDEX FROM Satellite`, and `SHOW TRIGGERS`. Do not expose credentials on screen.

### Classification of takeaways

- **Verified facts:** table and SQL object definitions in repository; CLI/TUI code paths; local DB version/object inventory/row counts/indexes; one successful safe SELECT test; one query plan and timing sample; no cloud deployment or app-level login/roles in inspected files.
- **Inferred design:** intended 3NF/BCNF under listed key dependencies; disjoint satellite subtypes as a modeling intent; “current orbit parameters” based on one row per satellite and upsert behavior.
- **Proposed improvements:** correct the report/PPT to match actual SQL; separate NEOs from orbital debris; persist source/measurement provenance; enforce subtype exclusivity/domain checks if required; improve least-privilege credentials and TLS before remote deployment; add repeatable tests and benchmark harness if course evidence requires them.
- **Unknown / not verified:** the original provenance of the report’s sample outputs; whether a separate production/cloud DB exists; any manually applied scripts outside the inspected local database; benchmark behavior under cold cache/concurrent load; correctness of heuristic conjunction scores against trusted orbital assessment.
