"""Menu-driven CLI for the Orbital Intelligence System with modern terminal UI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mysql.connector import Error as MySQLError

from app.db import init_schema, ping, settings
from app import queries
from app import art
from app import ui
from ingest.celestrak import sync as ingest_sync


def print_table(rows: list[dict], columns: list[tuple[str, str]] | None = None, title: str | None = None) -> None:
    """Wrapper for backward compatibility, delegates to ui.print_box_table."""
    ui.print_box_table(rows, columns=columns, title=title)


def banner(show_art: bool = True, art_mode: str = "clean") -> None:
    cfg = settings()
    print()
    if show_art:
        print(art.render_satellite_art(mode=art_mode, indent=2))
        print()

    b = lambda t: ui.color(t, ui.CLR_BORDER)
    w = 74
    inner = w - 2

    title_text = "[#] ORBITAL INTELLIGENCE SYSTEM"
    sub_text = "MariaDB Telemetry & Orbital Dynamics Tracking Catalog"
    db_text = f"Connected: {cfg['database']} @ {cfg['host']}:{cfg['port']}"

    top = "  " + b("╭" + "─" * inner + "╮")
    line1 = "  " + b("│") + ui.pad(ui.color(ui.pad(title_text, inner, align="center"), ui.CLR_CYAN_BOLD), inner) + b("│")
    line2 = "  " + b("│") + ui.pad(ui.color(ui.pad(sub_text, inner, align="center"), ui.CLR_DIM), inner) + b("│")
    line3 = "  " + b("│") + ui.pad(ui.color(ui.pad(db_text, inner, align="center"), ui.CLR_BLUE), inner) + b("│")
    bot = "  " + b("╰" + "─" * inner + "╯")

    print(top)
    print(line1)
    print(line2)
    print(line3)
    print(bot)


def menu() -> None:
    b = lambda t: ui.color(t, ui.CLR_BORDER)
    c_num = lambda t: ui.color(f"[{t}]", ui.CLR_CYAN_BOLD)
    w = 74
    inner = w - 2

    def menu_row(num: str, icon: str, title: str, desc: str) -> str:
        row_content = f"  {c_num(num)} {icon} {ui.color(title.ljust(21), ui.CLR_BOLD)} {ui.color(desc, ui.CLR_DIM)}"
        return "  " + b("│") + ui.pad(row_content, inner) + b("│")

    top = "\n  " + b("╭─") + ui.color(" [#] MISSION CONTROL CONSOLE ", ui.CLR_HEADER) + b("─" * (w - 32)) + b("╮")
    bot = "  " + b("╰" + "─" * inner + "╯")

    print(top)
    print(menu_row("1", "[>]", "View Satellite", "Detailed telemetry card by NORAD/Name"))
    print(menu_row("2", "[#]", "Search Fleet", "Filter by Operator, Status, or Type"))
    print(menu_row("3", "[!]", "Collision Alerts", "Active proximity warnings & risk"))
    print(menu_row("4", "[*]", "Sync Telemetry", "Multi-source ingest or cache"))
    print(menu_row("5", "[%]", "Analytics & Reports", "Fleet stats, debris & trajectory"))
    print(menu_row("6", "[o]", "Satellite ASCII Art", "Render spacecraft in multiple modes"))
    print(menu_row("q", "[x]", "Exit Console", "Close session and return to shell"))
    print(bot)


def view_flow() -> None:
    prompt = ui.color("  ▸ Enter NORAD ID or satellite name: ", ui.CLR_CYAN)
    term = input(prompt).strip()
    if not term:
        print(ui.color("  [!] No search term entered.", ui.CLR_YELLOW))
        return
    sat = queries.view_satellite(term)
    if not sat:
        print(ui.color(f"  [x] No satellite matched '{term}'.", ui.CLR_RED))
        return

    ui.print_telemetry_card(sat)

    if sat.get("recent_positions"):
        print()
        ui.print_box_table(
            sat["recent_positions"],
            [
                ("Observed_At", "Observed At (UTC)"),
                ("Lat", "Latitude"),
                ("Lon", "Longitude"),
                ("Alt_km", "Altitude (km)"),
            ],
            title="RECENT ORBITAL SAMPLES",
        )

    if sat.get("maneuvers"):
        print()
        ui.print_box_table(
            sat["maneuvers"],
            [("Maneuver_At", "Timestamp"), ("Type", "Maneuver Type"), ("Notes", "Flight Log Notes")],
            title="LOGGED ORBITAL MANEUVERS",
        )


def search_flow() -> None:
    print(ui.color("\n  ▸ Filter Satellite Catalog (Press ENTER to leave blank):", ui.CLR_BLUE_BOLD))
    op = input(ui.color("    Operator substring : ", ui.CLR_CYAN)).strip() or None
    st = input(ui.color("    Status [active/inactive/decayed] : ", ui.CLR_CYAN)).strip() or None
    ty = input(ui.color("    Type [comm/nav/eo/sci] : ", ui.CLR_CYAN)).strip() or None

    rows = queries.search_satellites(operator=op, status=st, sat_type=ty)
    print()
    ui.print_box_table(
        rows,
        [
            ("NORAD_ID", "NORAD"),
            ("Name", "Satellite Name"),
            ("Operator", "Operator"),
            ("Status", "Status"),
            ("Sat_Type", "Type"),
            ("Inclination", "Incl"),
            ("Apogee_km", "Apogee (km)"),
            ("Perigee_km", "Perigee (km)"),
        ],
        title=f"SEARCH RESULTS ({len(rows)} SATELLITES)",
    )


def alerts_flow() -> None:
    recalc_prompt = ui.color("  ▸ Recalculate conjunctions from latest positions? [y/N]: ", ui.CLR_YELLOW)
    refresh = input(recalc_prompt).strip().lower()
    if refresh == "y":
        made = queries.generate_collision_alerts()
        print(ui.color(f"  ✔ Generated and logged {len(made)} conjunction alert(s).", ui.CLR_GREEN))

    rows = queries.list_alerts()
    print()
    ui.print_box_table(
        rows,
        [
            ("Detected_At", "Timestamp (UTC)"),
            ("Status", "Conjunction_Status"),
            ("Object_A", "Object Alpha"),
            ("Object_B", "Object Bravo"),
            ("Distance_km", "Separation (km)"),
            ("Probability", "Collision Prob"),
        ],
        title=f"ACTIVE CONJUNCTION ALERTS ({len(rows)} EVENTS)",
    )


def sync_flow() -> None:
    from ingest.apis import run_multi_source_sync
    prompt = ui.color(
        "  ▸ Source [all/celestrak/satnogs/open_notify/nasa/n2yo/spacetrack/cache] (default: all): ",
        ui.CLR_CYAN,
    )
    choice = input(prompt).strip().lower() or "all"
    live = (choice != "cache")

    def cli_progress(step: int, total: int, msg: str, pct: float) -> None:
        print(ui.color(f"  [{step}/{total}] {msg}", ui.CLR_BLUE))

    result = run_multi_source_sync(source=choice, live=live, progress_callback=cli_progress)
    print(
        ui.color(
            f"  ✔ Sync {result['status'].upper()}: {result['upserted']} satellites, "
            f"{result['history']} positions, {result['debris']} debris "
            f"(Sources: {', '.join(result.get('sources', []))})",
            ui.CLR_GREEN,
        )
    )
    for warning in result.get("source_errors", []):
        print(ui.color(f"  ! {warning}", ui.CLR_YELLOW))
    print(ui.color(f"  ✔ Automated collision scan: {result['conjunctions']} close pairs evaluated and stored.", ui.CLR_CYAN))


def report_flow() -> None:
    b = lambda t: ui.color(t, ui.CLR_BORDER)
    w = 64
    inner = w - 2

    top = "\n  " + b("╭─") + ui.color(" 📊 ANALYTICAL REPORTS ", ui.CLR_HEADER) + b("─" * (w - 24)) + b("╮")
    bot = "  " + b("╰" + "─" * inner + "╯")

    print(top)
    print("  " + b("│") + ui.pad(f"  {ui.color('[a]', ui.CLR_CYAN_BOLD)} Fleet Breakdown by Operator", inner) + b("│"))
    print("  " + b("│") + ui.pad(f"  {ui.color('[b]', ui.CLR_CYAN_BOLD)} Fleet Breakdown by Satellite Type", inner) + b("│"))
    print("  " + b("│") + ui.pad(f"  {ui.color('[c]', ui.CLR_CYAN_BOLD)} Orbital Debris Historical Accumulation", inner) + b("│"))
    print("  " + b("│") + ui.pad(f"  {ui.color('[d]', ui.CLR_CYAN_BOLD)} Linear Trajectory Extrapolation (+N min)", inner) + b("│"))
    print("  " + b("│") + ui.pad(f"  {ui.color('[e]', ui.CLR_CYAN_BOLD)} Telemetry Sync Audit Log", inner) + b("│"))
    print("  " + b("│") + ui.pad(f"  {ui.color('[back]', ui.CLR_DIM)} Return to Main Console", inner) + b("│"))
    print(bot)

    pick = input(ui.color("  ▸ Select report [a-e]: ", ui.CLR_CYAN)).strip().lower()
    print()
    if pick == "a":
        ui.print_box_table(
            queries.report_by_operator(),
            [("Operator", "Fleet Operator"), ("Satellites", "Total Satellites"), ("Active", "Active Operational")],
            title="OPERATOR FLEET DISTRIBUTION",
        )
    elif pick == "b":
        ui.print_box_table(
            queries.report_by_type(),
            [("Sat_Type", "Mission Type"), ("Satellites", "Total Satellites")],
            title="SATELLITE MISSION SPECIALIZATIONS",
        )
    elif pick == "c":
        ui.print_box_table(
            queries.report_debris_trend(),
            [("Day", "Catalog Date"), ("Debris_Count", "Tracked Objects"), ("Status", "Tracking Status")],
            title="SPACE DEBRIS OBSERVATIONS",
        )
    elif pick == "d":
        term = input(ui.color("  ▸ Enter NORAD ID or satellite name: ", ui.CLR_CYAN)).strip()
        min_in = input(ui.color("  ▸ Extrapolation minutes ahead (default 10): ", ui.CLR_CYAN)).strip()
        minutes_n = int(min_in) if min_in.isdigit() else 10
        proj = queries.project_trajectory(term, minutes=minutes_n)
        if not proj:
            print(ui.color(f"  [x] No satellite matched '{term}'.", ui.CLR_RED))
            return
        if "reason" in proj:
            print(ui.color(f"  [!] {proj['reason']}", ui.CLR_YELLOW))
            return

        print(ui.color(f"  🛰️  {proj['Name']} (NORAD {proj['NORAD_ID']}) — Projected +{proj['minutes']} Minutes Ahead", ui.CLR_CYAN_BOLD))
        rows = [
            {"Point": "T-Older", **{k: proj["from"][k] for k in ("Observed_At", "Lat", "Lon", "Alt_km")}},
            {"Point": "T-Latest", **{k: proj["to"][k] for k in ("Observed_At", "Lat", "Lon", "Alt_km")}},
            {
                "Point": "T-Projected",
                "Observed_At": proj.get("projected_at"),
                **proj["projected"],
            },
        ]
        ui.print_box_table(
            rows,
            [
                ("Point", "Reference Point"),
                ("Observed_At", "Timestamp"),
                ("Lat", "Latitude"),
                ("Lon", "Longitude"),
                ("Alt_km", "Altitude (km)"),
            ],
            title="TRAJECTORY EXTRAPOLATION",
        )
        print(ui.color(f"  Note: {proj['note']}", ui.CLR_DIM))
    elif pick == "e":
        ui.print_box_table(
            queries.sync_log(),
            [
                ("Sync_ID", "ID"),
                ("Synced_At", "Timestamp (UTC)"),
                ("Source", "Ingest Source"),
                ("Rows_Upserted", "Upserted"),
                ("Status", "Status"),
            ],
            title="TELEMETRY INGESTION AUDIT LOG",
        )
    elif pick in ("back", "q", "exit", ""):
        return
    else:
        print(ui.color("  [!] Unknown report selection.", ui.CLR_YELLOW))


def art_flow() -> None:
    print(ui.color("\n  ▸ Satellite ASCII Art Showcase Modes:", ui.CLR_BLUE_BOLD))
    print(f"    {ui.color('[1]', ui.CLR_CYAN_BOLD)} Clean Spaces (High-contrast cyan & electric blue gradient)")
    print(f"    {ui.color('[2]', ui.CLR_CYAN_BOLD)} Cosmic Stars (Deep-space background dots with glowing satellite)")
    print(f"    {ui.color('[3]', ui.CLR_CYAN_BOLD)} Raw Verbatim (Original characters exactly as supplied)")
    mode_in = input(ui.color("  ▸ Select rendering mode [1-3] (default 1): ", ui.CLR_CYAN)).strip()
    mode_map = {"1": "clean", "2": "stars", "3": "raw", "clean": "clean", "stars": "stars", "raw": "raw"}
    selected_mode = mode_map.get(mode_in, "clean")
    print()
    print(art.render_satellite_art(mode=selected_mode, indent=2))
    print()


def loop() -> None:
    ping()
    banner(show_art=True, art_mode="clean")
    while True:
        menu()
        prompt = ui.color("  ois › ", ui.CLR_CYAN_BOLD)
        choice = input(prompt).strip().lower()
        print()
        try:
            if choice in ("1", "view"):
                view_flow()
            elif choice in ("2", "search"):
                search_flow()
            elif choice in ("3", "alerts"):
                alerts_flow()
            elif choice in ("4", "sync"):
                sync_flow()
            elif choice in ("5", "report", "reports"):
                report_flow()
            elif choice in ("6", "art", "ascii"):
                art_flow()
            elif choice in ("q", "7", "exit", "quit"):
                print(ui.color("  👋 Disconnecting from Orbital Intelligence System. Goodbye.", ui.CLR_CYAN))
                return
            else:
                print(ui.color("  [!] Invalid choice. Please enter 1–6 or 'q' to exit.", ui.CLR_YELLOW))
        except MySQLError as exc:
            print(ui.color(f"  [x] Database error: {exc}", ui.CLR_RED))
        except FileNotFoundError as exc:
            print(ui.color(f"  [x] File error: {exc}", ui.CLR_RED))
        except KeyboardInterrupt:
            print(ui.color("\n  [!] Operation interrupted by user.", ui.CLR_YELLOW))
        except Exception as exc:
            print(ui.color(f"  [x] Unexpected error: {exc}", ui.CLR_RED))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orbital Intelligence System CLI")
    parser.add_argument("--init-db", action="store_true", help="Apply schema.sql + seed.sql")
    parser.add_argument("--sync", action="store_true", help="Run ingest (cache unless --live)")
    parser.add_argument("--live", action="store_true", help="With --sync, fetch CelesTrak")
    parser.add_argument("--alerts", action="store_true", help="Recalculate and print alerts")
    parser.add_argument(
        "--report",
        choices=["operator", "type", "debris", "sync"],
        help="Print a report and exit",
    )
    parser.add_argument("--view", metavar="TERM", help="View a satellite by NORAD or name")
    parser.add_argument("--art", action="store_true", help="Render the satellite ASCII art and exit")
    parser.add_argument(
        "--art-style",
        choices=["clean", "stars", "raw"],
        default="clean",
        help="Visual style for satellite ASCII art (clean, stars, raw)",
    )
    parser.add_argument("--tui", action="store_true", help="Launch full interactive Terminal User Interface (default)")
    parser.add_argument("--cli", action="store_true", help="Launch the legacy text menu loop instead of the TUI")
    args = parser.parse_args(argv)

    if args.art:
        print(art.render_satellite_art(mode=args.art_style, indent=2))
        return 0

    if args.init_db:
        init_schema()
        print(ui.color("✔ Schema and seed applied successfully.", ui.CLR_GREEN))
        return 0

    ping()

    if args.sync:
        result = ingest_sync(live=args.live)
        print(
            ui.color(
                f"Sync {result['status'].upper()}: {result['upserted']} satellites, "
                f"{result['history']} history, {result['debris']} debris ({result['source']})",
                ui.CLR_GREEN,
            )
        )
        queries.generate_collision_alerts()

    if args.view:
        sat = queries.view_satellite(args.view)
        if not sat:
            print(ui.color(f"[x] No satellite matched '{args.view}'.", ui.CLR_RED))
            return 1
        ui.print_telemetry_card(sat)
        if sat.get("recent_positions"):
            print()
            ui.print_box_table(
                sat["recent_positions"],
                [
                    ("Observed_At", "Observed At (UTC)"),
                    ("Lat", "Latitude"),
                    ("Lon", "Longitude"),
                    ("Alt_km", "Altitude (km)"),
                ],
                title="RECENT ORBITAL SAMPLES",
            )
        return 0

    if args.alerts:
        if not args.sync:
            queries.generate_collision_alerts()
        rows = queries.list_alerts()
        ui.print_box_table(
            rows,
            [
                ("Detected_At", "Timestamp (UTC)"),
                ("Status", "Conjunction_Status"),
                ("Object_A", "Object Alpha"),
                ("Object_B", "Object Bravo"),
                ("Distance_km", "Separation (km)"),
                ("Probability", "Collision Prob"),
            ],
            title=f"ACTIVE CONJUNCTION ALERTS ({len(rows)} EVENTS)",
        )
        return 0

    if args.report:
        if args.report == "operator":
            ui.print_box_table(
                queries.report_by_operator(),
                [("Operator", "Fleet Operator"), ("Satellites", "Total Satellites"), ("Active", "Active Operational")],
                title="OPERATOR FLEET DISTRIBUTION",
            )
        elif args.report == "type":
            ui.print_box_table(
                queries.report_by_type(),
                [("Sat_Type", "Mission Type"), ("Satellites", "Total Satellites")],
                title="SATELLITE MISSION SPECIALIZATIONS",
            )
        elif args.report == "debris":
            ui.print_box_table(
                queries.report_debris_trend(),
                [("Day", "Catalog Date"), ("Debris_Count", "Tracked Objects"), ("Status", "Tracking Status")],
                title="SPACE DEBRIS OBSERVATIONS",
            )
        elif args.report == "sync":
            ui.print_box_table(
                queries.sync_log(),
                [
                    ("Sync_ID", "ID"),
                    ("Synced_At", "Timestamp (UTC)"),
                    ("Source", "Ingest Source"),
                    ("Rows_Upserted", "Upserted"),
                    ("Status", "Status"),
                ],
                title="TELEMETRY INGESTION AUDIT LOG",
            )
        return 0

    if args.sync:
        return 0

    if args.cli:
        loop()
        return 0

    from app import tui
    return tui.run()


if __name__ == "__main__":
    raise SystemExit(main())
