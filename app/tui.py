"""Minimalist Terminal User Interface (TUI) for Orbital Intelligence System.

Features:
- Clean, near-empty Home Page with clean space ASCII art and minimal menu:
  [1] Dashboard, [2] SQL Shell, [3] Settings, [q] Exit.
- Operational Dashboard (Fleet Catalog, Conjunction Alerts, Analytical Reports, Multi-API Ingestion).
- Interactive SQL Shell to execute arbitrary queries and view tabular results.
- Settings Panel for API access, database credentials, and storage paths.
- Multi-API telemetry ingestion (CelesTrak, SatNOGS, WhereTheISS, NASA NeoWs) with live multi-step progress bar.
- Refined, minimalistic aesthetic using geometric symbols instead of emojis.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from rich.style import Style
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    ProgressBar,
    RadioSet,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
    Tree,
)
from textual.widgets._select import SelectCurrent, SelectOverlay

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import art, queries
from app.config import load_config, save_config
from app.db import execute_raw_sql, get_connection, ping, settings
from ingest.apis import run_multi_source_sync


class SyncCancelled(Exception):
    """Raised when the user cancels an in-progress catalog fetch."""


class SQLQueryEditor(TextArea):
    """Multiline SQL editor with shell history at its vertical boundaries."""

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        super().__init__(text, **kwargs)
        self.command_history: list[str] = []
        self.history_index: int | None = None
        self._history_draft = ""

    async def _on_key(self, event) -> None:
        # TextArea handles Enter in its private key handler, before a normal
        # ``on_key`` handler can intercept it. Override that handler directly.
        if event.key in ("enter", "ctrl+m", "shift+enter", "shift+ctrl+m"):
            event.prevent_default()
            event.stop()
            if event.key.startswith("shift+") or not self.text.rstrip().endswith(";"):
                self.insert("\n")
            else:
                self.app.execute_shell_query(self.text)
            return
        await super()._on_key(event)

    def on_key(self, event) -> None:
        row, _ = self.cursor_location
        lines = self.text.splitlines() or [""]
        if event.key == "up" and row == 0 and self.command_history:
            event.prevent_default()
            event.stop()
            if self.history_index is None:
                self._history_draft = self.text
                self.history_index = len(self.command_history)
            self.history_index = max(0, self.history_index - 1)
            self.load_text(self.command_history[self.history_index])
            return
        if event.key == "down" and self.history_index is not None and row >= len(lines) - 1:
            event.prevent_default()
            event.stop()
            self.history_index = min(len(self.command_history), self.history_index + 1)
            value = self.command_history[self.history_index] if self.history_index < len(self.command_history) else self._history_draft
            self.load_text(value)
            if self.history_index == len(self.command_history):
                self.history_index = None
            return


class StripedSelectOverlay(SelectOverlay):
    """Select popup with a subtle alternate background on its option rows."""

    def render_line(self, y):
        strip = super().render_line(y)
        line_number = self.scroll_offset.y + y
        try:
            option_index, _ = self._lines[line_number]
        except IndexError:
            return strip
        if option_index % 2 == 1 and option_index != self.highlighted and option_index != self._mouse_hovering_over:
            return strip.apply_style(Style(bgcolor="#151027"))
        return strip


class StripedSelect(Select):
    """Select control using the application's lightly striped popup."""

    def compose(self) -> ComposeResult:
        yield SelectCurrent(self.prompt)
        yield StripedSelectOverlay(type_to_search=self._type_to_search).data_bind(
            compact=Select.compact
        )

def fmt_val(val: Any) -> str:
    """Format database values cleanly."""
    if val is None:
        return "—"
    if isinstance(val, (float, Decimal)):
        return f"{float(val):.4f}".rstrip("0").rstrip(".")
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M UTC")
    return str(val)


def fmt_status(status: Any) -> Text:
    s = str(status).lower() if status else ""
    if s == "active":
        return Text("● ACTIVE", style="bold green")
    if s == "inactive":
        return Text("○ INACTIVE", style="bold yellow")
    if s == "decayed":
        return Text("✕ DECAYED", style="bold red")
    if s == "tracked":
        return Text("● TRACKED", style="bold cyan")
    return Text(str(status), style="dim")


def fmt_alert_status(status: Any) -> Text:
    s = str(status).lower() if status else ""
    if s == "open":
        return Text("■ OPEN", style="bold red")
    if s == "watch":
        return Text("▲ WATCH", style="bold yellow")
    if s == "cleared":
        return Text("✔ CLEARED", style="bold green")
    if s == "expired":
        return Text("⊘ EXPIRED", style="dim")
    return Text(str(status), style="dim")


def fmt_prob(prob: Any) -> Text:
    try:
        p = float(prob)
        p_str = f"{p:.2f}%"
        if p >= 90.0:
            return Text(p_str, style="bold red")
        if p >= 70.0:
            return Text(p_str, style="bold yellow")
        return Text(p_str, style="bold green")
    except (ValueError, TypeError):
        return Text(fmt_val(prob))


def osis_block_logo() -> Text:
    """Build a compact block-letter O.S.I.S logo with an offset shadow."""
    glyphs = {
        "O": ("01110", "10001", "10001", "10001", "01110"),
        "S": ("01111", "10000", "01110", "00001", "11110"),
        "I": ("111", "010", "010", "010", "111"),
        ".": ("0", "0", "0", "0", "1"),
    }
    pattern: list[str] = []
    for index, char in enumerate("O.S.I.S"):
        glyph = glyphs[char]
        if index:
            pattern = [line + "0" for line in pattern]
        if not pattern:
            pattern = ["" for _ in range(5)]
        pattern = [line + part for line, part in zip(pattern, glyph)]

    height = len(pattern) + 1
    width = len(pattern[0]) + 1
    face = {(row, col) for row, line in enumerate(pattern) for col, bit in enumerate(line) if bit == "1"}
    shadow = {
        (row + 1, col + 1)
        for row, col in face
        if row + 1 < height and col + 1 < width and (row + 1, col + 1) not in face
    }

    logo = Text()
    for row in range(height):
        for col in range(width):
            if (row, col) in face:
                char, style = "█", "bold #facc15"
            elif (row, col) in shadow:
                char, style = "░", "bold #9333ea"
            else:
                char, style = " ", ""
            logo.append(char * 2, style=style)
        if row < height - 1:
            logo.append("\n")
    return logo


def block_ascii_word(word: str) -> Text:
    """Render a short heading in the dashboard's compact block style."""
    glyphs = {
        "A": ("01110", "10001", "11111", "10001", "10001"),
        "B": ("11110", "10001", "11110", "10001", "11110"),
        "D": ("11110", "10001", "10001", "10001", "11110"),
        "E": ("11111", "10000", "11110", "10000", "11111"),
        "G": ("01111", "10000", "10111", "10001", "01110"),
        "H": ("10001", "10001", "11111", "10001", "10001"),
        "I": ("111", "010", "010", "010", "111"),
        "L": ("10000", "10000", "10000", "10000", "11111"),
        "N": ("10001", "11001", "10101", "10011", "10001"),
        "O": ("01110", "10001", "10001", "10001", "01110"),
        "R": ("11110", "10001", "11110", "10100", "10010"),
        "S": ("01111", "10000", "01110", "00001", "11110"),
        "T": ("11111", "00100", "00100", "00100", "00100"),
        "Y": ("10001", "10001", "01010", "00100", "00100"),
    }
    letters = [glyphs[char] for char in word.upper() if char in glyphs]
    logo = Text()
    for row in range(5):
        for index, glyph in enumerate(letters):
            for bit in glyph[row]:
                logo.append(("░" if bit == "1" else " ") * 2, style="bold #9333ea")
            if index < len(letters) - 1:
                logo.append(" ")
        if row < 4:
            logo.append("\n")
    return logo


def _format_mysql_table(columns: list[str], rows: list[tuple[Any, ...]]) -> list[str]:
    """Format query results with the ASCII borders used by the MariaDB client."""
    values = [[fmt_val(value) for value in row] for row in rows]
    widths = [
        max([len(str(column)), *(len(row[index]) for row in values)])
        for index, column in enumerate(columns)
    ]
    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    lines = [border, "|" + "|".join(f" {str(column):<{width}} " for column, width in zip(columns, widths)) + "|", border]
    lines.extend("|" + "|".join(f" {value:<{width}} " for value, width in zip(row, widths)) + "|" for row in values)
    lines.append(border)
    return lines


class HelpModal(ModalScreen):
    """Minimalist Mission Control Operator Guide."""

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog-box"):
            yield Label("operator guide", id="help-title")
            yield Static(
                "\n"
                "[bold yellow]global navigation[/]\n"
                "  [yellow]esc / h[/]    return to home page\n"
                "  [yellow]1[/]          home: dashboard | dashboard: catalog | analysis: scans\n"
                "  [yellow]2[/]          home: analysis  | dashboard: alerts  | analysis: reports\n"
                "  [yellow]3[/]          home: sql shell | dashboard: reports | analysis: trajectory\n"
                "  [yellow]4[/]          home: settings  | dashboard: ingest\n"
                "  [yellow]/[/]          focus search filter in catalog\n"
                "  [yellow]r[/]          refresh current view / recalculate alerts\n"
                "  [yellow]?[/]          show this guide\n"
                "  [yellow]q[/]          quit\n\n"
                "[bold magenta]sql shell[/]\n"
                "  • enter multiline sql; Shift+Enter adds a line and Enter runs after a trailing semicolon.\n"
                "  • use ↑/↓ for lines and command history; toggle raw output for MariaDB-style results.\n"
                "  • use quick chips (tables, satellites, etc.) or type custom sql.\n\n"
                "[bold yellow]telemetry ingest[/]\n"
                "  • multi-source integration with celestrak gp, satnogs db, wheretheiss live, nasa neows.\n"
                "  • live progress bar and step tracking during updates.\n",
            )
            yield Button("close [esc]", id="help-close-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "help-close-btn":
            self.app.pop_screen()

    def key_escape(self) -> None:
        self.app.pop_screen()


class OISApp(App):
    """Orbital Intelligence System Minimalist TUI."""

    TITLE = "orbital intelligence system"
    SUB_TITLE = "mission control"
    CSS_PATH = "tui.tcss"

    BINDINGS = [
        Binding("escape", "handle_escape", "home", priority=True),
        Binding("h", "switch_view('home')", "home", show=False),
        Binding("1", "handle_key_1", "1", show=False),
        Binding("2", "handle_key_2", "2", show=False),
        Binding("3", "handle_key_3", "3", show=False),
        Binding("4", "handle_key_4", "4", show=False),
        Binding("/", "focus_search", "search"),
        Binding("r", "refresh_current_view", "refresh"),
        Binding("question_mark", "show_help", "help"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = load_config()
        self.current_view = "home"
        self.cached_satellites: list[dict] = []
        self.displayed_satellite_norads: list[str] = []
        self.pending_delete_norads: list[str] = []
        self.current_report_subview = "op"
        self.db_connected = False
        self.shell_output_mode = "neat"
        self.shell_raw_history: list[str] = []
        self.sync_pause_event = threading.Event()
        self.sync_pause_event.set()
        self.sync_cancel_event = threading.Event()
        self.sync_running = False
        self.current_analysis_report = "op"
        self.current_analysis_scan = "conjunction"
        self.analysis_findings: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        # Top status bar (clean, borderless top bar without bulky Header)
        with Horizontal(id="top-status-bar"):
            yield Button("< home", id="top-nav-btn")
            yield Label("orbital intelligence system", id="top-title")
            yield Label("● connecting", id="db-status-badge")
            yield Label("satellites: — · active: — · alerts: —", id="top-stats-summary")

        # VIEW 1: HOME PAGE (Near-empty with clean space ASCII art and menu)
        with Vertical(id="home-view-container"):
            with Horizontal(id="home-content-row"):
                with Vertical(id="home-art-box"):
                    yield Static("", id="home-art-content")
                with Vertical(id="home-menu-box"):
                    yield Static("", id="home-menu-title")
                    yield Label("Orbital Intelligence systems", id="home-menu-subtitle")
                    yield Button("1 · dashboard", id="menu-btn-dashboard", classes="home-menu-btn")
                    yield Button("2 · analysis", id="menu-btn-analysis", classes="home-menu-btn")
                    yield Button("3 · shell", id="menu-btn-shell", classes="home-menu-btn")
                    yield Button("4 · settings", id="menu-btn-settings", classes="home-menu-btn")
                    yield Button("q · exit", id="menu-btn-exit", classes="home-menu-btn")
                    yield Label("press 1-4 to switch · q to exit", id="home-menu-hint")

        # VIEW 2: DASHBOARD
        with Vertical(id="dashboard-view-container"):
            with Horizontal(id="dashboard-header"):
                with Vertical(id="dashboard-header-title"):
                    yield Static(block_ascii_word("dashboard"), id="dashboard-ascii-title")
                    yield Button("back to homepage", id="btn-home-dashboard", classes="back-home-btn")
                with Vertical(id="dashboard-header-actions"):
                    with Vertical(id="catalog-fetch-box"):
                        with Horizontal(id="catalog-fetch-controls"):
                            yield StripedSelect(
                                [
                                    ("local", "cache"),
                                    ("CelesTrak GP", "celestrak"),
                                    ("SatNOGS DB", "satnogs"),
                                    ("WhereTheISS", "open_notify"),
                                    ("NASA NeoWs", "nasa"),
                                    ("N2YO", "n2yo"),
                                    ("Space-Track", "spacetrack"),
                                    ("all sources", "all"),
                                    ("All APIs", "all_apis"),
                                ],
                                value="all",
                                id="catalog-fetch-source",
                                classes="filter-select",
                            )
                            yield Button("fetch", id="btn-catalog-fetch")
                            yield Button("pause", id="btn-catalog-fetch-pause", disabled=True)
                            yield Button("cancel", id="btn-catalog-fetch-cancel", disabled=True)
                        yield Label("idle", id="catalog-fetch-status")
                        yield ProgressBar(total=100, show_eta=False, id="catalog-fetch-progress")
                        yield Static("", id="catalog-fetch-error")
                    with Vertical(id="catalog-delete-box"):
                        with Horizontal(id="catalog-delete-row"):
                            yield Button("delete displayed records", id="btn-delete-displayed")
                            yield Input(placeholder="type DELETE", id="catalog-delete-confirm-input")
                            yield Button("confirm", id="btn-confirm-delete", disabled=True)
                            yield Button("cancel", id="btn-cancel-delete")
            with TabbedContent(id="main-tabs"):
                # TAB 1: FLEET CATALOG
                with TabPane("catalog [1]", id="tab-catalog"):
                    with Horizontal(id="catalog-filter-bar"):
                        yield Input(placeholder="search name / norad...", id="catalog-search")
                        yield StripedSelect(
                            [("all statuses", "ALL"), ("active", "active"), ("inactive", "inactive"), ("decayed", "decayed")],
                            value="ALL",
                            id="filter-status",
                            classes="filter-select",
                        )
                        yield StripedSelect(
                            [("all types", "ALL"), ("comm", "comm"), ("nav", "nav"), ("earth obs", "eo"), ("science", "sci")],
                            value="ALL",
                            id="filter-type",
                            classes="filter-select",
                        )
                        yield StripedSelect(
                            [("all operators", "ALL")],
                            value="ALL",
                            id="filter-operator",
                            classes="filter-select",
                        )
                        yield Button("reset", id="btn-reset-filters")

                    with Horizontal(id="catalog-split-container"):
                        with Vertical(id="catalog-left-column"):
                            with Vertical(id="catalog-table-box"):
                                yield DataTable(id="table-satellites", cursor_type="row")
                                yield Label("0 records", id="catalog-record-count")
                        with Vertical(id="catalog-right-column"):
                            with ScrollableContainer(id="satellite-detail-pane"):
                                yield Static("[dim italic]select a satellite to inspect telemetry...[/]", id="detail-card-content")
                            with Vertical(id="schema-explorer-box"):
                                yield Tree("ois_db", id="schema-explorer")

                # TAB 2: COLLISION ALERTS
                with TabPane("alerts [2]", id="tab-alerts"):
                    with Horizontal(id="alerts-action-bar"):
                        yield Label("loading conjunction alerts...", id="alerts-summary-label")
                        yield Button("recalculate", id="btn-recalc-alerts")
                        yield Button("refresh", id="btn-refresh-alerts")

                    with Horizontal(id="alerts-split-container"):
                        with Vertical(id="alerts-table-box"):
                            yield DataTable(id="table-alerts", cursor_type="row")
                        with ScrollableContainer(id="alert-detail-pane"):
                            yield Static("[dim italic]select an alert to view risk assessment...[/]", id="alert-card-content")

                # TAB 3: ANALYTICS & REPORTS
                with TabPane("reports [3]", id="tab-reports"):
                    with Horizontal(id="reports-nav-bar"):
                        yield Button("operator", id="btn-rep-op", classes="rep-nav-btn -active")
                        yield Button("types", id="btn-rep-type", classes="rep-nav-btn")
                        yield Button("debris", id="btn-rep-debris", classes="rep-nav-btn")
                        yield Button("trajectory", id="btn-rep-traj", classes="rep-nav-btn")
                        yield Button("audit log", id="btn-rep-log", classes="rep-nav-btn")

                    with Vertical(id="reports-content-box"):
                        yield DataTable(id="table-reports", cursor_type="row")
                        with Vertical(id="traj-controls-box"):
                            with Horizontal(id="traj-controls-row"):
                                yield Input(placeholder="norad id / name (e.g. 25544)", id="traj-input-sat")
                                yield Input(placeholder="minutes (10)", id="traj-input-min", value="10")
                                yield Button("extrapolate", id="btn-run-trajectory")
                            with ScrollableContainer(id="traj-result-box"):
                                yield Static("[dim italic]enter a satellite norad id / name and click extrapolate.[/]", id="traj-result-text")

                # TAB 4: INGEST & SYNC WITH PROGRESS BAR
                with TabPane("sync [4]", id="tab-sync"):
                    with Vertical(id="sync-controls-box"):
                        with Horizontal(id="sync-source-row"):
                            yield Label("[dim]source:[/] ")
                            yield StripedSelect(
                                [
                                    ("all sources (celestrak + satnogs + iss)", "all"),
                                    ("celestrak gp", "celestrak"),
                                    ("satnogs db", "satnogs"),
                                    ("wheretheiss live", "open_notify"),
                                    ("nasa neows", "nasa"),
                                    ("n2yo", "n2yo"),
                                    ("space-track", "spacetrack"),
                                    ("local cache", "cache"),
                                ],
                                value="all",
                                id="select-sync-source",
                                classes="filter-select",
                            )
                            yield Button("run sync", id="btn-trigger-sync")

                        yield Label("step 0/5: idle", id="sync-step-label")
                        yield ProgressBar(total=100, show_eta=False, id="sync-progress-bar")

                    yield RichLog(id="sync-richlog", highlight=True, markup=True)

        # VIEW 3: ANALYSIS
        with Vertical(id="analysis-view-container"):
            with Horizontal(id="analysis-header"):
                with Vertical(id="analysis-header-title"):
                    yield Static(block_ascii_word("analysis"), id="analysis-ascii-title")
                    yield Button("back to homepage", id="btn-home-analysis", classes="back-home-btn")
            with TabbedContent(id="analysis-tabs"):
                with TabPane("scans [1]", id="analysis-scans-tab"):
                    with Horizontal(id="analysis-scan-controls"):
                        yield Button("close approaches", id="btn-scan-conjunction", classes="analysis-scan-btn")
                        yield Button("low perigee", id="btn-scan-low-perigee", classes="analysis-scan-btn")
                        yield Button("high eccentricity", id="btn-scan-eccentricity", classes="analysis-scan-btn")
                        yield Button("missing data", id="btn-scan-data-gaps", classes="analysis-scan-btn")
                    yield Label("Choose a scan to see affected satellites and their details.", id="analysis-scan-summary")
                    with Horizontal(id="analysis-scan-layout"):
                        with Vertical(id="analysis-scan-table-box"):
                            yield DataTable(id="analysis-scan-table", cursor_type="row")
                        with ScrollableContainer(id="analysis-scan-detail-box"):
                            yield Static("[dim italic]Select a finding to inspect the affected satellite(s).[/]", id="analysis-scan-detail")

                with TabPane("fleet reports [2]", id="analysis-reports-tab"):
                    with Horizontal(id="analysis-report-nav"):
                        yield Button("operator", id="btn-analysis-report-op", classes="analysis-report-btn -active")
                        yield Button("types", id="btn-analysis-report-type", classes="analysis-report-btn")
                        yield Button("debris", id="btn-analysis-report-debris", classes="analysis-report-btn")
                        yield Button("sync history", id="btn-analysis-report-log", classes="analysis-report-btn")
                    yield DataTable(id="analysis-report-table", cursor_type="row")

                with TabPane("trajectory [3]", id="analysis-trajectory-tab"):
                    with Horizontal(id="analysis-trajectory-controls"):
                        yield Input(placeholder="NORAD ID or satellite name", id="analysis-traj-input-sat")
                        yield Input(value="10", placeholder="minutes", id="analysis-traj-input-min")
                        yield Button("project", id="btn-run-analysis-trajectory")
                    with ScrollableContainer(id="analysis-traj-result-box"):
                        yield Static("[dim italic]Enter a satellite and run a projection.[/]", id="analysis-traj-result")

        # VIEW 4: SQL SHELL
        with Vertical(id="shell-view-container"):
            with Horizontal(id="shell-header"):
                with Vertical(id="shell-header-title"):
                    yield Static(block_ascii_word("shell"), id="shell-ascii-title")
                    yield Button("back to homepage", id="btn-home-shell", classes="back-home-btn")
                with Vertical(id="shell-top-controls"):
                    with Horizontal(id="shell-quick-row"):
                        yield Label("[dim]queries:[/] ")
                        yield Button("tables", id="chip-sql-tables", classes="shell-chip-btn")
                        yield Button("satellites", id="chip-sql-satellites", classes="shell-chip-btn")
                        yield Button("alerts", id="chip-sql-alerts", classes="shell-chip-btn")
                        yield Button("positions", id="chip-sql-positions", classes="shell-chip-btn")
                        yield Button("debris", id="chip-sql-debris", classes="shell-chip-btn")

                    with Horizontal(id="shell-input-row"):
                        yield SQLQueryEditor("", id="shell-query-input", soft_wrap=True, show_line_numbers=False)
                        yield Button("run", id="btn-run-query")

                    with Horizontal(id="shell-output-controls"):
                        yield Label("Shift+Enter adds a line · Enter runs after ; · ↑/↓ move through lines and history", id="shell-editor-hint")
                        yield Button("raw output", id="btn-toggle-output")
                    yield Label("ready to execute queries against mariadb ois_db.", id="shell-status-banner")

            with Vertical(id="shell-results-box"):
                shell_results = DataTable(id="shell-results-table", cursor_type="row", zebra_stripes=True)
                shell_results.add_column("Run a query to see results")
                yield shell_results
                yield RichLog(id="shell-raw-output", highlight=False, markup=False, wrap=False)

        # VIEW 5: SETTINGS
        with Vertical(id="settings-view-container"):
            yield Static(block_ascii_word("settings"), id="settings-ascii-title")
            yield Button("back to homepage", id="btn-home-settings", classes="back-home-btn")
            with ScrollableContainer(id="settings-panel"):
                yield Label("settings", classes="settings-section-hdr")

                yield Label("API access", classes="settings-section-hdr")
                with Horizontal(classes="settings-row"):
                    yield Label("NASA key (DEMO_KEY works):", classes="settings-label")
                    yield Input(value=self.config.get("nasa_api_key", "DEMO_KEY"), id="setting-nasa-key", classes="settings-input")

                with Horizontal(classes="settings-row"):
                    yield Label("N2YO API key:", classes="settings-label")
                    yield Input(value=self.config.get("n2yo_api_key", ""), placeholder="required for N2YO", id="setting-n2yo-key", classes="settings-input")

                with Horizontal(classes="settings-row"):
                    yield Label("space-track user:", classes="settings-label")
                    yield Input(value=self.config.get("spacetrack_identity", ""), placeholder="account email", id="setting-spacetrack-user", classes="settings-input")

                with Horizontal(classes="settings-row"):
                    yield Label("space-track password:", classes="settings-label")
                    yield Input(value=self.config.get("spacetrack_password", ""), password=True, id="setting-spacetrack-password", classes="settings-input")

                yield Label("database", classes="settings-section-hdr")
                with Horizontal(classes="settings-row"):
                    yield Label("host:", classes="settings-label")
                    yield Input(value=str(self.config.get("db_host", "127.0.0.1")), id="setting-db-host", classes="settings-input")

                with Horizontal(classes="settings-row"):
                    yield Label("port:", classes="settings-label")
                    yield Input(value=str(self.config.get("db_port", 3307)), id="setting-db-port", classes="settings-input")

                with Horizontal(classes="settings-row"):
                    yield Label("user:", classes="settings-label")
                    yield Input(value=str(self.config.get("db_user", "ois")), id="setting-db-user", classes="settings-input")

                with Horizontal(classes="settings-row"):
                    yield Label("name:", classes="settings-label")
                    yield Input(value=str(self.config.get("db_name", "ois_db")), id="setting-db-name", classes="settings-input")

                with Horizontal(classes="settings-row"):
                    yield Label("storage dir:", classes="settings-label")
                    yield Input(value=str(self.config.get("db_storage_dir", ROOT / "sql")), id="setting-db-dir", classes="settings-input")

                yield Button("save settings", id="btn-save-settings")

        yield Footer()

    def on_mount(self) -> None:
        """Initialize database check, load views, and render home art."""
        self._apply_current_theme()
        self._check_db_and_stats()
        self._render_home_art()
        self._init_catalog_table()
        self._init_alerts_table()
        self._init_reports_table()
        self._init_analysis_tables()
        self.query_one("#catalog-fetch-box", Vertical).border_title = "FETCH RECORDS"
        self.query_one("#catalog-delete-box", Vertical).border_title = "DELETE RECORDS"
        self.query_one("#catalog-delete-confirm-input", Input).value = ""
        self.query_one("#btn-confirm-delete", Button).disabled = True
        self.query_one("#satellite-detail-pane", ScrollableContainer).border_title = "SATELLITE INFO"
        self.query_one("#schema-explorer-box", Vertical).border_title = "DATABASE STRUCTURE"
        self.load_database_structure()
        self.load_catalog()
        self.load_alerts()
        self.load_report_subview("op")
        self.switch_view("home")

    # -------------------------------------------------------------------------
    # Screen & View Management
    # -------------------------------------------------------------------------

    def switch_view(self, view_name: str) -> None:
        if view_name == "dashboard":
            self._clear_delete_confirmation()
        self.current_view = view_name

        home_cnt = self.query_one("#home-view-container", Vertical)
        dash_cnt = self.query_one("#dashboard-view-container", Vertical)
        analysis_cnt = self.query_one("#analysis-view-container", Vertical)
        shell_cnt = self.query_one("#shell-view-container", Vertical)
        settings_cnt = self.query_one("#settings-view-container", Vertical)
        top_btn = self.query_one("#top-nav-btn", Button)

        home_cnt.display = (view_name == "home")
        dash_cnt.display = (view_name == "dashboard")
        analysis_cnt.display = (view_name == "analysis")
        shell_cnt.display = (view_name == "shell")
        settings_cnt.display = (view_name == "settings")

        top_btn.display = (view_name != "home")
        if view_name == "shell":
            self.query_one("#shell-query-input", SQLQueryEditor).focus()
        elif view_name == "analysis":
            self.load_analysis_alerts()
            self.load_analysis_report(self.current_analysis_report)

    def _apply_current_theme(self) -> None:
        theme = "minimal-dark"
        for cls in ("-theme-minimal-dark", "-theme-cyberpunk", "-theme-nord", "-theme-matrix"):
            self.screen.remove_class(cls)
        self.screen.add_class(f"-theme-{theme}")

    def _render_home_art(self) -> None:
        art_view = self.query_one("#home-art-content", Static)
        art_str = art.render_satellite_art(mode="clean", use_color=True, indent=0)
        art_view.update(Text.from_ansi(art_str))
        self.query_one("#home-art-box", Vertical).border_title = ""
        self.query_one("#home-menu-title", Static).update(osis_block_logo())

    # -------------------------------------------------------------------------
    # Database Status & Summary Banner
    # -------------------------------------------------------------------------

    def _check_db_and_stats(self) -> None:
        try:
            cfg = settings()
            ping()
            self.db_connected = True
            badge = self.query_one("#db-status-badge", Label)
            badge.update(f"[bold yellow]● online[/] [dim]({cfg['database']})[/]")

            sats = queries.search_satellites()
            active_cnt = sum(1 for s in sats if str(s.get("Status")).lower() == "active")
            alerts = queries.list_alerts()

            stats_lbl = self.query_one("#top-stats-summary", Label)
            stats_lbl.update(
                f"[dim]sats:[/] [bold magenta]{len(sats)}[/]  "
                f"[dim]active:[/] [bold yellow]{active_cnt}[/]  "
                f"[dim]alerts:[/] [bold red]{len(alerts)}[/]"
            )
        except Exception as exc:
            self.db_connected = False
            badge = self.query_one("#db-status-badge", Label)
            badge.update("[bold red]○ offline[/]")
            self.notify(f"Database offline: {exc}", severity="error", timeout=6)

    # -------------------------------------------------------------------------
    # Fleet Catalog
    # -------------------------------------------------------------------------

    def _init_catalog_table(self) -> None:
        table = self.query_one("#table-satellites", DataTable)
        table.clear(columns=True)
        table.add_columns("NORAD", "Satellite Name", "Operator", "Status", "Type", "Incl (°)", "Apogee (km)", "Perigee (km)")

    @work(thread=True)
    def load_database_structure(self) -> None:
        """Load table/view metadata and present it as an expandable tree."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    "SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_COLLATION, CREATE_TIME "
                    "FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                    "ORDER BY TABLE_TYPE, TABLE_NAME"
                )
                objects = cursor.fetchall()
                cursor.execute(
                    "SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, COLUMN_TYPE, "
                    "IS_NULLABLE, COLUMN_KEY, COLUMN_DEFAULT, EXTRA "
                    "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
                )
                columns = cursor.fetchall()
                cursor.close()
            self.call_from_thread(self._render_database_structure, objects, columns, None)
        except Exception as exc:
            self.call_from_thread(self._render_database_structure, [], [], str(exc))

    def _render_database_structure(self, objects, columns, error) -> None:
        tree = self.query_one("#schema-explorer", Tree)
        tree.clear()
        tree.root.set_label(f"{settings()['database']}  ·  {len(objects)} objects")
        if error:
            tree.root.add_leaf(f"Unable to load schema: {error}")
            return
        by_table: dict[str, list[dict[str, Any]]] = {}
        for column in columns:
            by_table.setdefault(column["TABLE_NAME"], []).append(column)
        for obj in objects:
            name = obj["TABLE_NAME"]
            kind = "view" if obj["TABLE_TYPE"] == "VIEW" else "table"
            props = [f"Type: {kind}"]
            if obj.get("ENGINE"):
                props.append(f"Engine: {obj['ENGINE']}")
            if obj.get("TABLE_COLLATION"):
                props.append(f"Collation: {obj['TABLE_COLLATION']}")
            node = tree.root.add(f"{name}  [{kind}]", data="\n".join(props), expand=False)
            for col in by_table.get(name, []):
                details = [
                    f"Column: {col['COLUMN_NAME']}", f"Type: {col['COLUMN_TYPE']}",
                    f"Nullable: {col['IS_NULLABLE']}", f"Key: {col['COLUMN_KEY'] or '—'}",
                    f"Default: {col['COLUMN_DEFAULT'] if col['COLUMN_DEFAULT'] is not None else '—'}",
                    f"Extra: {col['EXTRA'] or '—'}",
                ]
                node.add_leaf(f"{col['COLUMN_NAME']}  :  {col['COLUMN_TYPE']}", data="\n".join(details))
        tree.root.expand()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.control.id == "schema-explorer" and event.node.data:
            self.notify(str(event.node.data), title="Database object", timeout=8)

    def load_catalog(self) -> None:
        if not self.db_connected:
            return
        try:
            self.cached_satellites = queries.search_satellites()
            operators = sorted({s["Operator"] for s in self.cached_satellites if s.get("Operator")})
            op_select = self.query_one("#filter-operator", Select)
            op_options: list[tuple[str, str]] = [("All Operators", "ALL")] + [(op, op) for op in operators]
            op_select.set_options(op_options)
            self.apply_catalog_filters()
        except Exception as exc:
            self.notify(f"Failed to load catalog: {exc}", severity="error")

    def apply_catalog_filters(self) -> None:
        self._clear_delete_confirmation()
        table = self.query_one("#table-satellites", DataTable)
        table.clear()
        self.displayed_satellite_norads = []

        search_query = self.query_one("#catalog-search", Input).value.strip().lower()
        sel_status = self.query_one("#filter-status", Select).value
        sel_type = self.query_one("#filter-type", Select).value
        sel_operator = self.query_one("#filter-operator", Select).value

        for sat in self.cached_satellites:
            norad = str(sat.get("NORAD_ID") or "")
            name = str(sat.get("Name") or "")
            operator = str(sat.get("Operator") or "")
            status = str(sat.get("Status") or "").lower()
            sat_type = str(sat.get("Sat_Type") or "").lower()

            if search_query and (search_query not in norad.lower() and search_query not in name.lower()):
                continue
            if isinstance(sel_status, str) and sel_status != "ALL" and status != sel_status.lower():
                continue
            if isinstance(sel_type, str) and sel_type != "ALL" and sat_type != sel_type.lower():
                continue
            if isinstance(sel_operator, str) and sel_operator != "ALL" and operator != sel_operator:
                continue

            incl_str = f"{float(sat['Inclination']):.2f}" if sat.get("Inclination") is not None else "—"
            apo_str = f"{float(sat['Apogee_km']):,.1f}" if sat.get("Apogee_km") is not None else "—"
            peri_str = f"{float(sat['Perigee_km']):,.1f}" if sat.get("Perigee_km") is not None else "—"

            type_color = {
                "comm": "magenta",
                "nav": "yellow",
                "eo": "green",
                "sci": "yellow",
            }.get(sat_type, "white")

            table.add_row(
                Text(norad, style="bold yellow"),
                Text(name, style="bold white"),
                Text(operator or "—", style="dim"),
                fmt_status(status),
                Text(f"[{sat_type.upper()}]", style=f"bold {type_color}"),
                Text(incl_str, style="white"),
                Text(apo_str, style="magenta"),
                Text(peri_str, style="magenta"),
                key=norad,
            )
            self.displayed_satellite_norads.append(norad)

        count = table.row_count
        self.query_one("#catalog-record-count", Label).update(f"{count:,} records")

        if table.row_count > 0:
            table.move_cursor(row=0)
            first_norad = table.get_row_at(0)[0].plain
            self.display_satellite_details(first_norad)
        else:
            detail_pane = self.query_one("#detail-card-content", Static)
            detail_pane.update("[dim italic]No satellites match the current filter criteria.[/]")

    def display_satellite_details(self, norad: str) -> None:
        detail_pane = self.query_one("#detail-card-content", Static)
        sat = queries.view_satellite(norad)
        if not sat:
            detail_pane.update(f"[red]Could not load telemetry for NORAD {norad}[/]")
            return

        sat_type = str(sat.get("Sat_Type") or "untyped").upper()
        status_styled = fmt_status(sat.get("Status"))
        launch_date = fmt_val(sat.get("Launch_Date"))

        lines = [
            f"[bold yellow]{sat.get('Name')}[/] [magenta][{sat_type}][/]",
            f"[dim]NORAD Catalog ID:[/] [bold yellow]{sat.get('NORAD_ID')}[/]   [dim]Operator:[/] [bold]{sat.get('Operator') or '—'}[/]",
            f"[dim]Operational Status:[/] {status_styled.markup}   [dim]Launch Date:[/] [white]{launch_date}[/]",
            "",
            "[bold magenta]─── ORBITAL PARAMETERS ─────────────────────────────[/]",
            f"[dim]Inclination:[/]  [bold white]{fmt_val(sat.get('Inclination'))}°[/]    [dim]Eccentricity:[/] [bold white]{fmt_val(sat.get('Eccentricity'))}[/]",
            f"[dim]Apogee Alt:[/]   [bold yellow]{fmt_val(sat.get('Apogee_km'))} km[/]    [dim]Perigee Alt:[/]  [bold yellow]{fmt_val(sat.get('Perigee_km'))} km[/]",
            f"[dim]Epoch Ref:[/]    [white]{fmt_val(sat.get('Epoch'))}[/]",
        ]

        st_lower = sat.get("Sat_Type", "").lower()
        if st_lower in ("comm", "nav", "eo", "sci"):
            lines.append("")
            lines.append(f"[bold yellow]─── PAYLOAD SPECIFICATIONS ({sat_type}) ───────────[/]")
            if st_lower == "comm":
                lines.append(f"[dim]Frequency Band:[/] [bold]{fmt_val(sat.get('Band'))}[/]   [dim]Transponders:[/] [bold]{fmt_val(sat.get('Transponders'))}[/]")
            elif st_lower == "nav":
                lines.append(f"[dim]Constellation:[/]  [bold]{fmt_val(sat.get('Constellation'))}[/]   [dim]Signal:[/] [bold]{fmt_val(sat.get('Signal_Type'))}[/]")
            elif st_lower == "eo":
                res = sat.get("Resolution_m")
                res_str = f"{res} m" if res is not None else "—"
                lines.append(f"[dim]Optical Sensor:[/] [bold]{fmt_val(sat.get('Sensor'))}[/]   [dim]Resolution:[/] [bold]{res_str}[/]")
            elif st_lower == "sci":
                lines.append(f"[dim]Science Mission:[/] [bold]{fmt_val(sat.get('Mission'))}[/]   [dim]Instrument:[/] [bold]{fmt_val(sat.get('Instrument'))}[/]")

        positions = sat.get("recent_positions", [])
        lines.append("")
        lines.append(f"[bold magenta]─── RECENT ORBITAL SAMPLES ({len(positions)}) ────────────────[/]")
        if positions:
            for p in positions:
                t_str = p["Observed_At"].strftime("%m-%d %H:%M") if isinstance(p.get("Observed_At"), datetime) else str(p.get("Observed_At"))
                lines.append(
                    f"  [dim]{t_str}[/]  Lat: [white]{float(p['Lat']):>8.4f}°[/]  Lon: [white]{float(p['Lon']):>9.4f}°[/]  Alt: [yellow]{float(p['Alt_km']):>6.1f} km[/]"
                )
        else:
            lines.append("  [dim italic](no telemetry observations logged)[/]")

        maneuvers = sat.get("maneuvers", [])
        if maneuvers:
            lines.append("")
            lines.append(f"[bold magenta]─── LOGGED MANEUVERS ({len(maneuvers)}) ────────────────────[/]")
            for m in maneuvers:
                m_time = m["Maneuver_At"].strftime("%Y-%m-%d") if isinstance(m.get("Maneuver_At"), datetime) else str(m.get("Maneuver_At"))
                lines.append(f"  [yellow]{m_time}[/] [bold]{m.get('Type')}[/]: [dim]{m.get('Notes')}[/]")

        detail_pane.update("\n".join(lines))

    # -------------------------------------------------------------------------
    # Collision Alerts
    # -------------------------------------------------------------------------

    def _init_alerts_table(self) -> None:
        table = self.query_one("#table-alerts", DataTable)
        table.clear(columns=True)
        table.add_columns("Detected (UTC)", "Status", "Object Alpha", "Object Bravo", "Separation (km)", "Collision Risk")

    def _init_analysis_tables(self) -> None:
        self.query_one("#analysis-scan-table", DataTable).clear(columns=True)
        self.query_one("#analysis-report-table", DataTable).clear(columns=True)
        self.query_one("#analysis-scan-table-box", Vertical).border_title = "SCAN RESULTS"
        self.query_one("#analysis-scan-detail-box", ScrollableContainer).border_title = "AFFECTED SATELLITE(S)"

    def load_alerts(self) -> None:
        if not self.db_connected:
            return
        table = self.query_one("#table-alerts", DataTable)
        table.clear()
        try:
            alerts = queries.list_alerts()
            summary_lbl = self.query_one("#alerts-summary-label", Label)
            summary_lbl.update(f"[bold yellow][!] {len(alerts)} ACTIVE CONJUNCTION PROXIMITY ALERTS[/]")

            for a in alerts:
                t_str = a["Detected_At"].strftime("%Y-%m-%d %H:%M") if isinstance(a.get("Detected_At"), datetime) else str(a.get("Detected_At"))
                dist_str = f"{float(a['Distance_km']):,.3f}"
                table.add_row(
                    Text(t_str, style="white"),
                    fmt_alert_status(a.get("Status")),
                    Text(f"{a['Object_A']} ({a['NORAD_A']})", style="bold yellow"),
                    Text(f"{a['Object_B']} ({a['NORAD_B']})", style="bold yellow"),
                    Text(f"{dist_str} km", style="bold magenta"),
                    fmt_prob(a.get("Probability")),
                    key=str(a.get("Alert_ID")),
                )

            if alerts:
                table.move_cursor(row=0)
                self.display_alert_details(0)
            else:
                card = self.query_one("#alert-card-content", Static)
                card.update("[dim italic]No active conjunction alerts logged.[/]")
        except Exception as exc:
            self.notify(f"Failed to load alerts: {exc}", severity="error")

    def display_alert_details(self, row_idx: int) -> None:
        table = self.query_one("#table-alerts", DataTable)
        card = self.query_one("#alert-card-content", Static)
        try:
            row = table.get_row_at(row_idx)
            detected = row[0].plain
            status_text = row[1]
            obj_a = row[2].plain
            obj_b = row[3].plain
            separation = row[4].plain
            prob_text = row[5]

            lines = [
                "[bold red][!] CONJUNCTION PROXIMITY ADVISORY[/]",
                f"[dim]Detected Timestamp:[/] [white]{detected}[/]",
                f"[dim]Conjunction Status:[/] {status_text.markup}",
                "",
                "[bold magenta]─── OBJECT GEOMETRY ────────────────────────────────[/]",
                f"  [bold yellow]Object Alpha:[/] {obj_a}",
                f"  [bold yellow]Object Bravo:[/] {obj_b}",
                "",
                "[bold yellow]─── MISS DISTANCE & ESTIMATED RISK ─────────────────[/]",
                f"  [dim]Estimated Separation:[/] [bold magenta]{separation}[/]",
                f"  [dim]Collision Probability:[/] {prob_text.markup}",
                "",
                "[dim]Advisory Note:[/] ECEF Euclidean vector modeling indicates close pass.",
            ]
            card.update("\n".join(lines))
        except Exception:
            pass

    def recalculate_alerts(self) -> None:
        try:
            made = queries.generate_collision_alerts()
            self.load_alerts()
            if self.current_analysis_scan == "conjunction":
                self.load_analysis_alerts()
            self._check_db_and_stats()
            self.notify(f"Generated and logged {len(made)} conjunction alert(s)", severity="information")
        except Exception as exc:
            self.notify(f"Alert calculation failed: {exc}", severity="error")

    def load_analysis_alerts(self) -> None:
        self.run_analysis_scan("conjunction", recalculate=False)

    def run_analysis_scan(self, scan: str, *, recalculate: bool = True) -> None:
        self.current_analysis_scan = scan
        self.analysis_findings = []
        table = self.query_one("#analysis-scan-table", DataTable)
        table.clear(columns=True)
        detail = self.query_one("#analysis-scan-detail", Static)
        detail.update("[dim italic]Select a finding to inspect the affected satellite(s).[/]")

        if not self.db_connected:
            self.query_one("#analysis-scan-summary", Label).update("Database unavailable; scans require a database connection.")
            return

        try:
            if scan == "conjunction":
                if recalculate:
                    queries.generate_collision_alerts()
                    self.load_alerts()
                    self._check_db_and_stats()
                rows = queries.list_alerts()
                table.add_columns("Detected (UTC)", "Status", "Object A", "Object B", "Distance", "Risk score")
                for index, row in enumerate(rows):
                    self.analysis_findings.append({"kind": scan, **row})
                    detected = row["Detected_At"]
                    detected_text = detected.strftime("%Y-%m-%d %H:%M") if isinstance(detected, datetime) else str(detected)
                    table.add_row(
                        detected_text,
                        fmt_alert_status(row.get("Status")),
                        f"{row['Object_A']} ({row['NORAD_A']})",
                        f"{row['Object_B']} ({row['NORAD_B']})",
                        f"{float(row['Distance_km']):,.3f} km",
                        fmt_prob(row.get("Probability")),
                        key=str(index),
                    )
                summary = f"{len(rows)} active close-approach findings · based on stored latest positions; risk score is heuristic"

            elif scan in ("low-perigee", "eccentricity"):
                satellites = queries.search_satellites()
                if scan == "low-perigee":
                    table.add_columns("NORAD", "Satellite", "Status", "Perigee km", "Apogee km", "Inclination °")
                    affected = [
                        sat for sat in satellites
                        if sat.get("Perigee_km") is not None and float(sat["Perigee_km"]) < 200
                    ]
                    affected.sort(key=lambda sat: float(sat["Perigee_km"]))
                    summary = f"{len(affected)} satellites with perigee below 200 km · screening threshold only"
                else:
                    table.add_columns("NORAD", "Satellite", "Status", "Eccentricity", "Perigee km", "Apogee km")
                    affected = [
                        sat for sat in satellites
                        if sat.get("Eccentricity") is not None and float(sat["Eccentricity"]) >= 0.1
                    ]
                    affected.sort(key=lambda sat: float(sat["Eccentricity"]), reverse=True)
                    summary = f"{len(affected)} satellites with eccentricity ≥ 0.10 · screening threshold only"

                for index, sat in enumerate(affected):
                    self.analysis_findings.append({"kind": scan, **sat})
                    table.add_row(
                        sat.get("NORAD_ID", ""),
                        sat.get("Name", ""),
                        fmt_status(sat.get("Status")),
                        fmt_val(sat.get("Perigee_km")) if scan == "low-perigee" else fmt_val(sat.get("Eccentricity")),
                        fmt_val(sat.get("Apogee_km")) if scan == "low-perigee" else fmt_val(sat.get("Perigee_km")),
                        fmt_val(sat.get("Inclination")) if scan == "low-perigee" else fmt_val(sat.get("Apogee_km")),
                        key=str(index),
                    )

            elif scan == "data-gaps":
                table.add_columns("NORAD", "Satellite", "Status", "Data issue", "Last position")
                with get_connection() as conn:
                    cursor = conn.cursor(dictionary=True)
                    cursor.execute(
                        """
                        SELECT s.NORAD_ID, s.Name, s.Operator, s.Status,
                               o.Satellite_ID AS Has_Orbit,
                               latest.Observed_At AS Last_Position
                        FROM Satellite s
                        LEFT JOIN Orbit_Parameters o ON o.Satellite_ID = s.Satellite_ID
                        LEFT JOIN (
                            SELECT Satellite_ID, MAX(Observed_At) AS Observed_At
                            FROM Position_History GROUP BY Satellite_ID
                        ) latest ON latest.Satellite_ID = s.Satellite_ID
                        WHERE o.Satellite_ID IS NULL OR latest.Observed_At IS NULL
                        ORDER BY s.Name
                        LIMIT 1000
                        """
                    )
                    affected = cursor.fetchall()
                    cursor.close()
                for index, sat in enumerate(affected):
                    issues = []
                    if sat.get("Has_Orbit") is None:
                        issues.append("missing orbit parameters")
                    if sat.get("Last_Position") is None:
                        issues.append("missing position history")
                    sat["Issue"] = "; ".join(issues)
                    self.analysis_findings.append({"kind": scan, **sat})
                    last_position = sat.get("Last_Position")
                    table.add_row(
                        sat.get("NORAD_ID", ""),
                        sat.get("Name", ""),
                        fmt_status(sat.get("Status")),
                        sat["Issue"],
                        last_position.strftime("%Y-%m-%d %H:%M") if isinstance(last_position, datetime) else "none",
                        key=str(index),
                    )
                summary = f"{len(affected)} satellites missing orbital or position data (showing up to 1,000)"
            else:
                return

            self.query_one("#analysis-scan-summary", Label).update(summary)
            if self.analysis_findings:
                table.move_cursor(row=0)
                self.display_analysis_finding(0)
            else:
                detail.update("[dim italic]No satellites matched this scan.[/]")
        except Exception as exc:
            self.query_one("#analysis-scan-summary", Label).update(f"Scan failed: {exc}")
            self.notify(f"Analysis scan failed: {exc}", severity="error")

    def display_analysis_finding(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self.analysis_findings):
            return
        finding = self.analysis_findings[row_index]
        lines: list[str] = []
        try:
            if finding["kind"] == "conjunction":
                lines.extend([
                    "[bold red]CLOSE-APPROACH FINDING[/]",
                    f"[dim]Distance:[/] {float(finding['Distance_km']):,.3f} km",
                    f"[dim]Heuristic score:[/] {fmt_val(finding.get('Probability'))}%",
                    f"[dim]Status:[/] {finding.get('Status')}",
                    "[dim]This is a latest-position screen, not a future conjunction prediction.[/]",
                    "",
                ])
                profiles = [
                    (finding.get("NORAD_A"), finding.get("Object_A")),
                    (finding.get("NORAD_B"), finding.get("Object_B")),
                ]
            else:
                profiles = [(finding.get("NORAD_ID"), finding.get("Name"))]
                lines.extend([
                    f"[bold yellow]{finding.get('Issue') or 'SCAN MATCH'}[/]" if finding["kind"] == "data-gaps" else "[bold yellow]SCAN MATCH[/]",
                    f"[dim]Scan:[/] {finding['kind']}",
                    "",
                ])

            for norad, fallback_name in profiles:
                sat = queries.view_satellite(str(norad)) if norad else None
                if not sat:
                    lines.append(f"[bold yellow]{fallback_name or norad}[/]")
                    lines.append(f"[dim]NORAD:[/] {norad or '—'}")
                    continue
                lines.extend([
                    f"[bold yellow]{sat.get('Name')}[/] [magenta][{str(sat.get('Sat_Type') or 'untyped').upper()}][/]",
                    f"[dim]NORAD:[/] {sat.get('NORAD_ID')}   [dim]Operator:[/] {sat.get('Operator') or '—'}",
                    f"[dim]Status:[/] {sat.get('Status')}   [dim]Launch:[/] {fmt_val(sat.get('Launch_Date'))}",
                    f"[dim]Inclination:[/] {fmt_val(sat.get('Inclination'))}°   [dim]Eccentricity:[/] {fmt_val(sat.get('Eccentricity'))}",
                    f"[dim]Perigee:[/] {fmt_val(sat.get('Perigee_km'))} km   [dim]Apogee:[/] {fmt_val(sat.get('Apogee_km'))} km",
                    f"[dim]Orbit epoch:[/] {fmt_val(sat.get('Epoch'))}",
                ])
                positions = sat.get("recent_positions", [])
                if positions:
                    latest = positions[0]
                    lines.append(
                        f"[dim]Latest position:[/] {fmt_val(latest.get('Observed_At'))} · "
                        f"{fmt_val(latest.get('Lat'))}, {fmt_val(latest.get('Lon'))}, {fmt_val(latest.get('Alt_km'))} km"
                    )
                lines.append("")
            self.query_one("#analysis-scan-detail", Static).update("\n".join(lines))
        except Exception as exc:
            self.query_one("#analysis-scan-detail", Static).update(f"[red]Could not load finding details: {exc}[/]")

    def load_analysis_report(self, mode: str) -> None:
        self.current_analysis_report = mode
        for button in self.query(".analysis-report-btn"):
            button.remove_class("-active")
        self.query_one(f"#btn-analysis-report-{mode}", Button).add_class("-active")
        table = self.query_one("#analysis-report-table", DataTable)
        table.clear(columns=True)

        try:
            if mode == "op":
                table.add_columns("Fleet Operator", "Total Satellites", "Active Operational", "Active Share")
                for row in queries.report_by_operator():
                    total = int(row["Satellites"])
                    active = int(row.get("Active") or 0)
                    share = f"{active / total * 100:.1f}%" if total else "0.0%"
                    table.add_row(row["Operator"], total, active, share)
            elif mode == "type":
                table.add_columns("Mission Type", "Specialization", "Total Satellites")
                type_names = {
                    "comm": "Communications & Relay",
                    "nav": "Global Navigation (GPS/Galileo/BeiDou)",
                    "eo": "Earth Observation & Remote Sensing",
                    "sci": "Space Science & Astronomy",
                    "untyped": "General Orbital Body",
                }
                for row in queries.report_by_type():
                    sat_type = str(row["Sat_Type"]).lower()
                    table.add_row(f"[{sat_type.upper()}]", type_names.get(sat_type, sat_type.title()), row["Satellites"])
            elif mode == "debris":
                table.add_columns("Catalog Date", "Tracked Space Debris Objects", "Status")
                for row in queries.report_debris_trend():
                    day = row.get("Day")
                    day_text = day.strftime("%Y-%m-%d") if isinstance(day, datetime) else str(day)
                    table.add_row(day_text, row["Debris_Count"], fmt_status(row.get("Status")))
            elif mode == "log":
                table.add_columns("Sync ID", "Timestamp (UTC)", "Ingest Source", "Rows Upserted", "Status")
                for row in queries.sync_log():
                    synced = row.get("Synced_At")
                    synced_text = synced.strftime("%Y-%m-%d %H:%M:%S") if isinstance(synced, datetime) else str(synced)
                    table.add_row(row["Sync_ID"], synced_text, row["Source"], row["Rows_Upserted"], fmt_status(row.get("Status")))
        except Exception as exc:
            self.notify(f"Analysis report failed: {exc}", severity="error")

    # -------------------------------------------------------------------------
    # Analytics & Reports
    # -------------------------------------------------------------------------

    def _init_reports_table(self) -> None:
        table = self.query_one("#table-reports", DataTable)
        table.clear(columns=True)

    def load_report_subview(self, mode: str) -> None:
        self.current_report_subview = mode
        for btn in self.query(".rep-nav-btn"):
            btn.remove_class("-active")
        btn_match = self.query_one(f"#btn-rep-{mode}", Button)
        btn_match.add_class("-active")

        table = self.query_one("#table-reports", DataTable)
        traj_box = self.query_one("#traj-controls-box", Vertical)

        if mode == "traj":
            table.display = False
            traj_box.display = True
            return

        table.display = True
        traj_box.display = False
        table.clear(columns=True)

        try:
            if mode == "op":
                table.add_columns("Fleet Operator", "Total Satellites", "Active Operational", "Active Share")
                rows = queries.report_by_operator()
                for r in rows:
                    tot = int(r["Satellites"])
                    act = int(r.get("Active") or 0)
                    pct = f"{(act / tot * 100):.1f}%" if tot > 0 else "0.0%"
                    table.add_row(
                        Text(str(r["Operator"]), style="bold white"),
                        Text(str(tot), style="magenta"),
                        Text(str(act), style="green"),
                        Text(pct, style="yellow"),
                    )

            elif mode == "type":
                table.add_columns("Mission Type", "Specialization", "Total Satellites")
                type_names = {
                    "comm": "Communications & Relay",
                    "nav": "Global Navigation (GPS/Galileo/BeiDou)",
                    "eo": "Earth Observation & Remote Sensing",
                    "sci": "Space Science & Astronomy",
                    "untyped": "General Orbital Body",
                }
                rows = queries.report_by_type()
                for r in rows:
                    st = str(r["Sat_Type"]).lower()
                    table.add_row(
                        Text(f"[{st.upper()}]", style="bold magenta"),
                        Text(type_names.get(st, st.title()), style="white"),
                        Text(str(r["Satellites"]), style="bold yellow"),
                    )

            elif mode == "debris":
                table.add_columns("Catalog Date", "Tracked Space Debris Objects", "Status")
                rows = queries.report_debris_trend()
                for r in rows:
                    day_str = r["Day"].strftime("%Y-%m-%d") if isinstance(r.get("Day"), datetime) else str(r.get("Day"))
                    table.add_row(
                        Text(day_str, style="white"),
                        Text(str(r["Debris_Count"]), style="bold red"),
                        fmt_status(r.get("Status")),
                    )

            elif mode == "log":
                table.add_columns("Sync ID", "Timestamp (UTC)", "Ingest Source", "Rows Upserted", "Status")
                rows = queries.sync_log()
                for r in rows:
                    t_str = r["Synced_At"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(r.get("Synced_At"), datetime) else str(r.get("Synced_At"))
                    table.add_row(
                        Text(str(r["Sync_ID"]), style="dim"),
                        Text(t_str, style="white"),
                        Text(str(r["Source"]), style="magenta"),
                        Text(str(r["Rows_Upserted"]), style="green"),
                        fmt_status(r.get("Status")),
                    )

        except Exception as exc:
            self.notify(f"Report query error: {exc}", severity="error")

    def run_trajectory_projection(
        self,
        input_id: str = "traj-input-sat",
        minutes_id: str = "traj-input-min",
        result_id: str = "traj-result-text",
    ) -> None:
        term = self.query_one(f"#{input_id}", Input).value.strip()
        min_str = self.query_one(f"#{minutes_id}", Input).value.strip()
        minutes = int(min_str) if min_str.isdigit() else 10

        out_view = self.query_one(f"#{result_id}", Static)
        if not term:
            self.notify("Please enter a satellite NORAD ID or Name", severity="warning")
            return

        try:
            proj = queries.project_trajectory(term, minutes=minutes)
            if not proj:
                out_view.update(f"[bold red]✕ No satellite found matching '{term}'.[/]")
                return
            if "reason" in proj:
                out_view.update(f"[bold yellow][!] {proj['reason']}[/]")
                return

            t_old = proj["from"]["Observed_At"].strftime("%Y-%m-%d %H:%M:%S")
            t_now = proj["to"]["Observed_At"].strftime("%Y-%m-%d %H:%M:%S")
            t_proj = proj["projected_at"].strftime("%Y-%m-%d %H:%M:%S") if "projected_at" in proj else "—"

            lines = [
                f"[bold yellow][>] TRAJECTORY EXTRAPOLATION: {proj['Name']} (NORAD {proj['NORAD_ID']})[/]",
                f"[dim]Projecting forward +{proj['minutes']} minutes into future orbit:[/]\n",
                "  [bold]Reference Point      Timestamp (UTC)       Latitude       Longitude      Altitude[/]",
                "  ─────────────────────────────────────────────────────────────────────────────────",
                f"  [dim]T-Older Sample:[/]   {t_old}   {float(proj['from']['Lat']):>10.4f}°   {float(proj['from']['Lon']):>10.4f}°   {float(proj['from']['Alt_km']):>8.2f} km",
                f"  [yellow]T-Latest Sample:[/]  {t_now}   {float(proj['to']['Lat']):>10.4f}°   {float(proj['to']['Lon']):>10.4f}°   {float(proj['to']['Alt_km']):>8.2f} km",
                f"  [bold green]T-PROJECTED:[/]      {t_proj}   [bold green]{float(proj['projected']['Lat']):>10.4f}°[/]   [bold green]{float(proj['projected']['Lon']):>10.4f}°[/]   [bold green]{float(proj['projected']['Alt_km']):>8.2f} km[/]",
                "\n[dim italic]Calculation Model: " + proj.get("note", "") + "[/]",
            ]
            out_view.update("\n".join(lines))
        except Exception as exc:
            self.notify(f"Trajectory extrapolation failed: {exc}", severity="error")

    # -------------------------------------------------------------------------
    # Ingest & Sync with Multi-step Progress
    # -------------------------------------------------------------------------

    @work(thread=True)
    def run_multi_api_sync_task(self, source: str, *, catalog_fetch: bool = False) -> None:
        log = self.query_one("#sync-richlog", RichLog)
        step_lbl = self.query_one("#sync-step-label", Label)
        p_bar = self.query_one("#sync-progress-bar", ProgressBar)
        btn = self.query_one("#btn-trigger-sync", Button)
        status = self.query_one("#catalog-fetch-status", Label) if catalog_fetch else None
        fetch_bar = self.query_one("#catalog-fetch-progress", ProgressBar) if catalog_fetch else None
        fetch_btn = self.query_one("#btn-catalog-fetch", Button) if catalog_fetch else None
        pause_btn = self.query_one("#btn-catalog-fetch-pause", Button) if catalog_fetch else None
        cancel_btn = self.query_one("#btn-catalog-fetch-cancel", Button) if catalog_fetch else None
        error_view = self.query_one("#catalog-fetch-error", Static) if catalog_fetch else None

        if catalog_fetch:
            self.sync_running = True
            self.sync_cancel_event.clear()
            self.sync_pause_event.set()
            fetch_btn.disabled = True
            pause_btn.disabled = False
            cancel_btn.disabled = False
            error_view.update("")
            fetch_bar.progress = 0
            status.update("starting…")
        else:
            btn.disabled = True
        log.write(f"\n[bold magenta]═════ INGESTION RUN STARTED (Source: {source.upper()}) ═════[/]")

        def update_progress(step: int, total_steps: int, msg: str, pct: float) -> None:
            self.app.call_from_thread(step_lbl.update, f"[bold yellow]Step {step}/{total_steps}:[/] {msg}")
            self.app.call_from_thread(setattr, p_bar, "progress", int(pct * 100))
            self.app.call_from_thread(log.write, f"[dim][{datetime.now().strftime('%H:%M:%S')}][/] {msg}")
            if catalog_fetch:
                self.app.call_from_thread(status.update, msg)
                self.app.call_from_thread(setattr, fetch_bar, "progress", int(pct * 100))

        def check_fetch_controls() -> None:
            if self.sync_cancel_event.is_set():
                raise SyncCancelled("Download cancelled by user")
            self.sync_pause_event.wait()
            if self.sync_cancel_event.is_set():
                raise SyncCancelled("Download cancelled by user")

        try:
            live = (source != "cache")
            result = run_multi_source_sync(
                source=source,
                live=live,
                progress_callback=update_progress,
                control_callback=check_fetch_controls if catalog_fetch else None,
            )

            log.write(f"[bold green]✔ Ingest Complete:[/] {result['upserted']} satellites, {result['history']} positions, {result['debris']} debris.")
            if result.get("sources"):
                log.write(f"  [yellow]APIs Consulted:[/] {', '.join(result['sources'])}")
            if result.get("source_errors"):
                warning_text = "\n".join(result["source_errors"])
                log.write(f"[bold yellow]Source warnings:[/] {warning_text}")
                if catalog_fetch:
                    self.app.call_from_thread(
                        error_view.update,
                        "[bold yellow]Source warnings:[/] " + "; ".join(result["source_errors"]),
                    )

            self.app.call_from_thread(self._refresh_all_data)
            if catalog_fetch and not result.get("upserted") and not result.get("sources"):
                message = "No records were returned. Check the network or local cache and try again."
                self.app.call_from_thread(status.update, "no records received")
                source_warnings = "; ".join(result.get("source_errors", []))
                detail = f"{message} {source_warnings}" if source_warnings else message
                self.app.call_from_thread(error_view.update, f"[bold red]Fetch failed:[/] {detail}")
                self.notify(message, severity="error")
            else:
                if catalog_fetch:
                    self.app.call_from_thread(status.update, "complete")
                self.notify("Telemetry sync complete!", severity="information")
        except SyncCancelled as exc:
            log.write(f"[yellow]↷ {exc}[/]")
            if catalog_fetch:
                self.app.call_from_thread(status.update, "cancelled")
        except Exception as exc:
            log.write(f"[bold red]✕ Ingest encountered error: {exc}[/]")
            if catalog_fetch:
                self.app.call_from_thread(status.update, "failed")
                self.app.call_from_thread(error_view.update, f"[bold red]Fetch failed:[/] {exc}")
            self.notify(f"Sync error: {exc}", severity="error")
        finally:
            if catalog_fetch:
                self.sync_running = False
                self.app.call_from_thread(setattr, fetch_btn, "disabled", False)
                self.app.call_from_thread(setattr, pause_btn, "disabled", True)
                self.app.call_from_thread(setattr, cancel_btn, "disabled", True)
                self.app.call_from_thread(setattr, pause_btn, "label", "pause")
                self.sync_pause_event.set()
            else:
                btn.disabled = False

    def _refresh_all_data(self) -> None:
        self._check_db_and_stats()
        self.load_catalog()
        self.load_alerts()
        self.load_report_subview(self.current_report_subview)

    # -------------------------------------------------------------------------
    # SQL Shell
    # -------------------------------------------------------------------------

    def execute_shell_query(self, query: str | None = None) -> None:
        inp = self.query_one("#shell-query-input", SQLQueryEditor)
        sql = query if query is not None else inp.text.strip()
        if not sql:
            self.notify("Please enter a SQL statement", severity="warning")
            return

        if sql not in inp.command_history:
            inp.command_history.append(sql)
        inp.history_index = None

        table = self.query_one("#shell-results-table", DataTable)
        banner = self.query_one("#shell-status-banner", Label)
        raw_output = self.query_one("#shell-raw-output", RichLog)
        table.clear(columns=True)

        res = execute_raw_sql(sql)
        raw_text = [f"MariaDB [ois_db]> {sql}"]
        if res["status"] == "ok":
            if "columns" in res:
                table.add_columns(*res["columns"])
                for row in res["rows"]:
                    table.add_row(*[fmt_val(cell) for cell in row])
                banner.update(f"[bold green]Query OK:[/] {res['row_count']} row(s) returned in {res['duration_ms']} ms")
                raw_text.extend(_format_mysql_table(res["columns"], res["rows"]))
                raw_text.append(f"{res['row_count']} row(s) in set ({res['duration_ms'] / 1000:.2f} sec)")
            else:
                banner.update(f"[bold green]{res.get('message', 'Query OK')}[/] in {res['duration_ms']} ms")
                table.add_column("Status")
                table.add_row(res.get("message", "Query OK"))
                raw_text.append(f"Query OK, {res.get('affected_rows', 0)} row(s) affected ({res['duration_ms'] / 1000:.2f} sec)")
        else:
            banner.update(f"[bold red]SQL Error:[/] {res.get('error', 'Execution failed')}")
            table.add_column("Status")
            table.add_row("SQL error; see status above")
            self.notify("SQL execution error", severity="error")
            raw_text.append(f"ERROR: {res.get('error', 'Execution failed')}")

        inp.load_text("")
        self.shell_raw_history.extend(raw_text + [""])
        raw_output.clear()
        for line in self.shell_raw_history:
            raw_output.write(line)

    def toggle_shell_output(self) -> None:
        self.shell_output_mode = "raw" if self.shell_output_mode == "neat" else "neat"
        raw_mode = self.shell_output_mode == "raw"
        self.query_one("#shell-results-table", DataTable).display = not raw_mode
        self.query_one("#shell-raw-output", RichLog).display = raw_mode
        self.query_one("#btn-toggle-output", Button).label = "neat output" if raw_mode else "raw output"

    # -------------------------------------------------------------------------
    # Settings Management
    # -------------------------------------------------------------------------

    def save_settings_panel(self) -> None:
        nasa_key = self.query_one("#setting-nasa-key", Input).value.strip()
        n2yo_key = self.query_one("#setting-n2yo-key", Input).value.strip()
        spacetrack_identity = self.query_one("#setting-spacetrack-user", Input).value.strip()
        spacetrack_password = self.query_one("#setting-spacetrack-password", Input).value
        db_host = self.query_one("#setting-db-host", Input).value.strip()
        db_port = self.query_one("#setting-db-port", Input).value.strip()
        db_user = self.query_one("#setting-db-user", Input).value.strip()
        db_name = self.query_one("#setting-db-name", Input).value.strip()
        db_dir = self.query_one("#setting-db-dir", Input).value.strip()

        updates: dict[str, Any] = {
            "nasa_api_key": nasa_key or "DEMO_KEY",
            "n2yo_api_key": n2yo_key,
            "spacetrack_identity": spacetrack_identity,
            "spacetrack_password": spacetrack_password,
            "db_host": db_host or "127.0.0.1",
            "db_port": int(db_port) if db_port.isdigit() else 3307,
            "db_user": db_user or "ois",
            "db_name": db_name or "ois_db",
            "db_storage_dir": db_dir,
        }

        self.config = save_config(updates)
        self._apply_current_theme()
        self._check_db_and_stats()
        self.notify("Configuration saved.", severity="information")

    # -------------------------------------------------------------------------
    # Event Handlers & Input Events
    # -------------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id in ("top-nav-btn", "btn-home-dashboard", "btn-home-analysis", "btn-home-shell", "btn-home-settings"):
            self.switch_view("home")

        # Home Menu Buttons
        elif btn_id == "menu-btn-dashboard":
            self.switch_view("dashboard")
        elif btn_id == "menu-btn-analysis":
            self.switch_view("analysis")
        elif btn_id == "menu-btn-shell":
            self.switch_view("shell")
        elif btn_id == "menu-btn-settings":
            self.switch_view("settings")
        elif btn_id == "menu-btn-exit":
            self.exit()

        # Catalog Buttons
        elif btn_id == "btn-delete-displayed":
            if self.query_one("#main-tabs", TabbedContent).active != "tab-catalog":
                self.notify("Open the catalog tab before deleting displayed records.", severity="warning")
                return
            self.pending_delete_norads = list(self.displayed_satellite_norads)
            if not self.pending_delete_norads:
                self.notify("There are no displayed satellite records to delete.", severity="warning")
            else:
                self.query_one("#catalog-delete-confirm-input", Input).focus()
                self.notify(
                    f"Ready to delete {len(self.pending_delete_norads):,} displayed records. Type DELETE to enable confirmation.",
                    severity="warning",
                )
        elif btn_id == "btn-confirm-delete":
            confirm_input = self.query_one("#catalog-delete-confirm-input", Input)
            if confirm_input.value != "DELETE":
                return
            if not self.pending_delete_norads:
                self.notify("Click delete displayed records first.", severity="warning")
                return
            norads = list(self.pending_delete_norads)
            confirm_input.disabled = True
            self.query_one("#btn-delete-displayed", Button).disabled = True
            self.query_one("#btn-confirm-delete", Button).disabled = True
            self.delete_displayed_satellites(norads)
        elif btn_id == "btn-cancel-delete":
            self._clear_delete_confirmation()
        elif btn_id == "btn-catalog-fetch":
            selected = self.query_one("#catalog-fetch-source", Select).value
            source = str(selected) if isinstance(selected, str) else "all"
            if source == "all_apis":
                source = "all"
            self.run_multi_api_sync_task(source=source, catalog_fetch=True)
        elif btn_id == "btn-catalog-fetch-pause":
            if self.sync_pause_event.is_set():
                self.sync_pause_event.clear()
                event.button.label = "resume"
                self.query_one("#catalog-fetch-status", Label).update("paused")
            else:
                self.sync_pause_event.set()
                event.button.label = "pause"
                self.query_one("#catalog-fetch-status", Label).update("resuming…")
        elif btn_id == "btn-catalog-fetch-cancel":
            self.sync_cancel_event.set()
            self.sync_pause_event.set()
            self.query_one("#catalog-fetch-status", Label).update("cancelling…")
        elif btn_id == "btn-reset-filters":
            self.query_one("#catalog-search", Input).value = ""
            self.query_one("#filter-status", Select).value = "ALL"
            self.query_one("#filter-type", Select).value = "ALL"
            self.query_one("#filter-operator", Select).value = "ALL"
            self.apply_catalog_filters()
            self.notify("Filters reset", timeout=2)

        # Alerts Buttons
        elif btn_id == "btn-recalc-alerts":
            self.recalculate_alerts()
        elif btn_id in ("btn-scan-conjunction", "btn-scan-low-perigee", "btn-scan-eccentricity", "btn-scan-data-gaps"):
            scan_modes = {
                "btn-scan-conjunction": "conjunction",
                "btn-scan-low-perigee": "low-perigee",
                "btn-scan-eccentricity": "eccentricity",
                "btn-scan-data-gaps": "data-gaps",
            }
            self.run_analysis_scan(scan_modes[btn_id])
        elif btn_id == "btn-refresh-alerts":
            self.load_alerts()
            self.notify("Alerts refreshed", timeout=2)

        elif btn_id in (
            "btn-analysis-report-op",
            "btn-analysis-report-type",
            "btn-analysis-report-debris",
            "btn-analysis-report-log",
        ):
            mode = btn_id.removeprefix("btn-analysis-report-")
            self.load_analysis_report(mode)

        # Reports Buttons
        elif btn_id in ("btn-rep-op", "btn-rep-type", "btn-rep-debris", "btn-rep-traj", "btn-rep-log"):
            mode_map = {
                "btn-rep-op": "op",
                "btn-rep-type": "type",
                "btn-rep-debris": "debris",
                "btn-rep-traj": "traj",
                "btn-rep-log": "log",
            }
            self.load_report_subview(mode_map[btn_id])

        elif btn_id == "btn-run-trajectory":
            self.run_trajectory_projection()
        elif btn_id == "btn-run-analysis-trajectory":
            self.run_trajectory_projection(
                input_id="analysis-traj-input-sat",
                minutes_id="analysis-traj-input-min",
                result_id="analysis-traj-result",
            )

        # Sync Button
        elif btn_id == "btn-trigger-sync":
            src = self.query_one("#select-sync-source", Select).value
            source = str(src) if isinstance(src, str) else "all"
            self.run_multi_api_sync_task(source=source)

        # SQL Shell Buttons & Chips
        elif btn_id == "btn-run-query":
            self.execute_shell_query()
        elif btn_id == "btn-toggle-output":
            self.toggle_shell_output()
        elif btn_id == "chip-sql-tables":
            inp = self.query_one("#shell-query-input", SQLQueryEditor)
            inp.load_text("SHOW TABLES;")
            self.execute_shell_query("SHOW TABLES;")
        elif btn_id == "chip-sql-satellites":
            inp = self.query_one("#shell-query-input", SQLQueryEditor)
            inp.load_text("SELECT NORAD_ID, Name, Operator, Status FROM Satellite LIMIT 10;")
            self.execute_shell_query(inp.text)
        elif btn_id == "chip-sql-alerts":
            inp = self.query_one("#shell-query-input", SQLQueryEditor)
            inp.load_text("SELECT Alert_ID, Distance_km, Probability, Status FROM Collision_Alerts LIMIT 10;")
            self.execute_shell_query(inp.text)
        elif btn_id == "chip-sql-positions":
            inp = self.query_one("#shell-query-input", SQLQueryEditor)
            inp.load_text("SELECT Satellite_ID, Observed_At, Lat, Lon, Alt_km FROM Position_History ORDER BY Observed_At DESC LIMIT 10;")
            self.execute_shell_query(inp.text)
        elif btn_id == "chip-sql-debris":
            inp = self.query_one("#shell-query-input", SQLQueryEditor)
            inp.load_text("SELECT Debris_ID, First_Seen, Status FROM Debris_Records LIMIT 10;")
            self.execute_shell_query(inp.text)

        # Settings Save Button
        elif btn_id == "btn-save-settings":
            self.save_settings_panel()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "catalog-search":
            self.apply_catalog_filters()
        elif event.input.id == "catalog-delete-confirm-input":
            self.query_one("#btn-confirm-delete", Button).disabled = event.value != "DELETE"

    def on_input_submitted(self, event: Input.Submitted) -> None:
        return

    @work(thread=True)
    def delete_displayed_satellites(self, norads: list[str]) -> None:
        try:
            deleted = 0
            with get_connection() as conn:
                cursor = conn.cursor()
                for start in range(0, len(norads), 500):
                    batch = norads[start : start + 500]
                    placeholders = ", ".join(["%s"] * len(batch))
                    cursor.execute(
                        f"DELETE FROM Satellite WHERE NORAD_ID IN ({placeholders})",
                        batch,
                    )
                    deleted += cursor.rowcount
                cursor.close()
            self.app.call_from_thread(self._finish_displayed_delete, deleted, None)
        except Exception as exc:
            self.app.call_from_thread(self._finish_displayed_delete, 0, str(exc))

    def _finish_displayed_delete(self, deleted: int, error: str | None) -> None:
        self.pending_delete_norads = []
        self.query_one("#catalog-delete-confirm-input", Input).disabled = False
        self.query_one("#catalog-delete-confirm-input", Input).value = ""
        self.query_one("#btn-confirm-delete", Button).disabled = True
        delete_button = self.query_one("#btn-delete-displayed", Button)
        delete_button.disabled = False
        if error:
            self.notify(f"Could not delete displayed records: {error}", severity="error")
            return
        self._refresh_all_data()
        self.notify(f"Deleted {deleted:,} displayed satellite records.", severity="information")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in ("filter-status", "filter-type", "filter-operator"):
            self.apply_catalog_filters()
        elif event.select.id == "catalog-fetch-source":
            self._clear_delete_confirmation()

    def _clear_delete_confirmation(self) -> None:
        """Clear the typed confirmation and discard its displayed-record snapshot."""
        self.pending_delete_norads = []
        confirm_input = self.query_one("#catalog-delete-confirm-input", Input)
        confirm_input.value = ""
        confirm_input.disabled = False
        self.query_one("#btn-confirm-delete", Button).disabled = True
        self.query_one("#btn-delete-displayed", Button).disabled = False

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "table-satellites":
            norad = event.data_table.get_row_at(event.cursor_row)[0].plain
            self.display_satellite_details(norad)
        elif event.data_table.id == "table-alerts":
            self.display_alert_details(event.cursor_row)
        elif event.data_table.id == "analysis-scan-table":
            self.display_analysis_finding(event.cursor_row)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "table-satellites" and event.cursor_row is not None:
            try:
                norad = event.data_table.get_row_at(event.cursor_row)[0].plain
                self.display_satellite_details(norad)
            except Exception:
                pass
        elif event.data_table.id == "table-alerts" and event.cursor_row is not None:
            try:
                self.display_alert_details(event.cursor_row)
            except Exception:
                pass
        elif event.data_table.id == "analysis-scan-table" and event.cursor_row is not None:
            self.display_analysis_finding(event.cursor_row)

    # -------------------------------------------------------------------------
    # Actions & Global Keybindings
    # -------------------------------------------------------------------------

    def action_handle_escape(self) -> None:
        if len(self.screen_stack) > 1:
            self.pop_screen()
        elif self.current_view != "home":
            self.switch_view("home")

    def action_handle_key_1(self) -> None:
        if self.current_view == "home":
            self.switch_view("dashboard")
        elif self.current_view == "dashboard":
            self.query_one("#main-tabs", TabbedContent).active = "tab-catalog"
        elif self.current_view == "analysis":
            self.query_one("#analysis-tabs", TabbedContent).active = "analysis-scans-tab"

    def action_handle_key_2(self) -> None:
        if self.current_view == "home":
            self.switch_view("analysis")
        elif self.current_view == "dashboard":
            self.query_one("#main-tabs", TabbedContent).active = "tab-alerts"
        elif self.current_view == "analysis":
            self.query_one("#analysis-tabs", TabbedContent).active = "analysis-reports-tab"

    def action_handle_key_3(self) -> None:
        if self.current_view == "home":
            self.switch_view("shell")
        elif self.current_view == "dashboard":
            self.query_one("#main-tabs", TabbedContent).active = "tab-reports"
        elif self.current_view == "analysis":
            self.query_one("#analysis-tabs", TabbedContent).active = "analysis-trajectory-tab"

    def action_handle_key_4(self) -> None:
        if self.current_view == "home":
            self.switch_view("settings")
        elif self.current_view == "dashboard":
            self.query_one("#main-tabs", TabbedContent).active = "tab-sync"

    def action_switch_view(self, view_name: str) -> None:
        self.switch_view(view_name)

    def action_focus_search(self) -> None:
        if self.current_view != "dashboard":
            self.switch_view("dashboard")
        self.query_one("#main-tabs", TabbedContent).active = "tab-catalog"
        self.query_one("#catalog-search", Input).focus()

    def action_refresh_current_view(self) -> None:
        self._check_db_and_stats()
        if self.current_view == "dashboard":
            tabs = self.query_one("#main-tabs", TabbedContent)
            active = tabs.active
            if active == "tab-catalog":
                self.load_catalog()
                self.notify("Catalog refreshed", timeout=2)
            elif active == "tab-alerts":
                self.recalculate_alerts()
            elif active == "tab-reports":
                self.load_report_subview(self.current_report_subview)
                self.notify("Reports refreshed", timeout=2)
        elif self.current_view == "analysis":
            active = self.query_one("#analysis-tabs", TabbedContent).active
            if active == "analysis-scans-tab":
                self.run_analysis_scan(
                    self.current_analysis_scan,
                    recalculate=(self.current_analysis_scan == "conjunction"),
                )
            elif active == "analysis-reports-tab":
                self.load_analysis_report(self.current_analysis_report)

    def action_show_help(self) -> None:
        self.push_screen(HelpModal())


def run() -> int:
    """Launch the Minimalist Mission Control TUI."""
    app = OISApp()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(run())
