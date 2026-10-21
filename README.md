# Orbital Intelligence System

MariaDB catalog for satellite tracking. Python pulls CelesTrak, stores history, and a CLI runs search, collision-style alerts, and reports.

See `architecture.md` for the shape of the system and `plan.md` for the build order.

## Stack

- MariaDB 11 (`ois_db`)
- Python 3.14+ with `requests` and `mysql-connector-python`
- CelesTrak GP JSON (cached under `cache/` while developing)

## Quick Start (Linux)

Clone and run the automated installer:

```bash
chmod +x setup.sh
./setup.sh
```

This automatically:
- Checks Python 3.14+ and Docker/Podman
- Configures `.env`
- Starts the MariaDB container (`docker compose up -d`)
- Sets up `.venv` and installs dependencies
- Bootstraps the database schema and ingests telemetry
- Installs the global `ois` and `satellites` commands in `~/.local/bin/` and creates `./ois`

Once installed, launch the system with `ois` or `./ois`.

## Quick Start (Windows)

The Windows setup runs Python natively and uses Docker Desktop for MariaDB.

Prerequisites:

- Windows 10 or 11
- Python 3.14 or newer, available as `python` in PowerShell
- Docker Desktop for Windows, running with Docker Compose available

From the project folder, open PowerShell and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

The setup script creates `.env` from `.env.example` if needed, creates `.venv`, installs Python dependencies, starts MariaDB, and initializes the schema and starter data when the database is reachable. It keeps an existing `.env` unchanged. If PowerShell blocks local scripts, the command above applies the execution policy only to that process.

Launch Mission Control or pass CLI options through the Windows launcher:

```powershell
.\ois.cmd
.\ois.cmd --alerts
.\ois.cmd --view 25544
.\ois.cmd --sync
```

To start MariaDB later, run `docker compose up -d` from the project folder. If initial database setup was skipped, run `.\ois.cmd --init-db` and then `.\ois.cmd --sync`.

---

## Manual Setup (Cross-Platform)

```bash
# 1. Database
docker compose up -d

# 2. Python env (Linux/macOS)
python3 -m venv .venv
source .venv/bin/activate    # On Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Environment
cp .env.example .env

# 4. Schema + dummy rows
python -m app --init-db

# 5. Ingest (multi-source satellite data)
python -m app --sync

# 6. Launch Mission Control TUI
python -m app    # Linux/macOS; Windows: .\.venv\Scripts\python.exe -m app
```

## Mission Control TUI

Running `ois` or `python -m app` launches a full-screen, interactive Terminal User Interface powered by Textual.

### Architecture & Navigation

The TUI opens to a near-empty, minimalistic **Home Page** displaying the clean spacecraft ASCII art and main menu:

1. **`[1] Dashboard`**:
   - **`[>] Fleet Catalog`**: Search by NORAD ID or name, filter by Orbit Status (`Active`, `Inactive`, `Decayed`), Mission Type (`Comm`, `Nav`, `EO`, `Sci`), or Fleet Operator. Split-pane shows Spacecraft Telemetry Card, orbital parameters, payload specs, recent orbital samples, and logged maneuvers.
   - **`[!] Collision Alerts`**: Conjunction proximity warning table with miss distance (km) and color-coded collision risk percentages. Recalculate conjunction warnings live from latest orbital vectors.
   - **`[#] Reports & Analytics`**: Multi-view reports for Fleet by Operator, Mission Types, Space Debris Accumulation, **Interactive Trajectory Extrapolation Tool** (projecting future lat/lon/alt), and Ingestion Audit Log.
   - **`[*] Ingest & Sync`**: Live multi-step ingestion with progress across CelesTrak, SatNOGS, WhereTheISS, NASA NeoWs, N2YO, and Space-Track.

2. **`[2] SQL Shell`**:
   - Interactive SQL query terminal against MariaDB `ois_db`.
   - Run arbitrary queries (`SELECT`, `SHOW`, `DESCRIBE`, `EXPLAIN`, `UPDATE`, etc.) with quick chips (`SHOW TABLES`, `SATELLITES`, `ALERTS`, `POSITIONS`, `DEBRIS`).
   - Dynamic results `DataTable`, query timer (ms), and row counter.

3. **`[3] Settings`**:
   - **Visual Themes**: Minimal Dark, Cyberpunk Cyan & Magenta, Nord Frost, Matrix Monochrome Green.
   - **API Keys**: Configure NASA API Key, N2YO API Key, and default ingestion source.
   - **Database & Storage**: Configure MariaDB host, port, user, database name, and storage/backup folder.

4. **`[Esc] / [h]`**: Return to Home Page from anywhere in the application.

### Keyboard Shortcuts

| Key | Context | Action |
|-----|---------|--------|
| `Esc` / `h` | Global | Return to Home Page |
| `1` | Home / Dash | Home: Open Dashboard \| Dash: Switch to Fleet Catalog |
| `2` | Home / Dash | Home: Open SQL Shell \| Dash: Switch to Collision Alerts |
| `3` | Home / Dash | Home: Open Settings \| Dash: Switch to Reports |
| `4` | Dashboard | Switch to Ingest & Sync |
| `/` | Dashboard | Quick-focus Catalog Search filter |
| `r` | Dashboard | Refresh current view / recalculate conjunction alerts |
| `?` | Global | Open Mission Control Operator Guide modal |
| `q` | Global | Quit Mission Control session |

### Command-line & Automation Flags

- `--tui`: Explicitly launch the Mission Control TUI (default).
- `--cli`: Launch the legacy scrolling text menu.
- `--view TERM`: Print telemetry card for a satellite by NORAD ID or name and exit.
- `--alerts`: Recalculate and print conjunction alerts table.
- `--report {operator,type,debris,sync}`: Print analytical reports in tabular format.
- `--sync [--live]`: Run multi-source telemetry ingestion.
- `--init-db`: Apply database schema and seed.
- `--art [--art-style clean|stars|raw]`: Render satellite ASCII art and exit.

## Layout

```
sql/schema.sql          tables, FKs, CASCADE
sql/seed.sql            dummy rows for a first demo
ingest/apis.py          multi-source ingestion (CelesTrak, SatNOGS, WhereTheISS, NASA)
ingest/celestrak.py     CelesTrak GP parser & legacy sync
app/tui.py              Textual Mission Control TUI (Home, Dashboard, Shell, Settings)
app/tui.tcss            TUI stylesheet
app/config.py           configuration & persistence manager (API access, DB)
app/cli.py              CLI flags & legacy menu
app/queries.py          alerts, reports, projection
cache/                  cached space telemetry while developing
tests/test_tui.py       Automated Textual TUI & SQL Shell test suite
docs/viva.md            viva talking points
docs/er.dbml            paste into dbdiagram.io
```

## Definition of done

A live MariaDB instance, one Python sync from CelesTrak (live or cached), and an interactive mission control TUI with collision alerts and trajectory analytics — without using the `mysql` shell.
