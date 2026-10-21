"""Automated verification suite for OIS Minimalist TUI, Home Page, Shell, and Settings."""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.tui import OISApp
from app.db import execute_raw_sql
from ingest.apis import run_multi_source_sync
from textual.widgets import Button, DataTable, Input, Static, TabbedContent


class TestOISMinimalistTUI(unittest.IsolatedAsyncioTestCase):
    """Test full interactive flows: Home Page, Dashboard, Shell, Settings."""

    async def test_home_page_and_navigation(self) -> None:
        app = OISApp()
        async with app.run_test(size=(120, 40)) as pilot:
            # 1. Starts on Home Page with clean aesthetics
            self.assertEqual(app.current_view, "home")
            # Verify no bulky Header widget exists
            self.assertEqual(len(app.query("Header")), 0, "Header widget should be removed")
            
            # Verify small clean button labels
            self.assertEqual(str(app.query_one("#menu-btn-dashboard", Button).label), "1 · dashboard")
            self.assertEqual(str(app.query_one("#menu-btn-shell", Button).label), "2 · shell")
            self.assertEqual(str(app.query_one("#menu-btn-settings", Button).label), "3 · settings")
            self.assertEqual(str(app.query_one("#menu-btn-exit", Button).label), "q · exit")
            self.assertEqual(str(app.query_one("#top-nav-btn", Button).label), "< home")

            art_text = app.query_one("#home-art-content", Static)
            self.assertGreater(len(str(art_text.render())), 100, "Home ASCII art should be rendered")

            # 2. Navigate to Dashboard via button press
            app.query_one("#menu-btn-dashboard", Button).press()
            await pilot.pause()
            self.assertEqual(app.current_view, "dashboard")

            # Check Catalog table
            catalog_table = app.query_one("#table-satellites", DataTable)
            self.assertGreater(catalog_table.row_count, 0)

            # Search in catalog
            search_input = app.query_one("#catalog-search", Input)
            search_input.value = "25544"
            await pilot.pause()
            self.assertEqual(catalog_table.row_count, 1)

            # Reset filters
            app.query_one("#btn-reset-filters", Button).press()
            await pilot.pause()
            self.assertGreater(catalog_table.row_count, 1)

            # Switch tabs in Dashboard
            await pilot.press("2")
            await pilot.pause()
            tabs = app.query_one("#main-tabs", TabbedContent)
            self.assertEqual(tabs.active, "tab-alerts")

            # 3. Return to Home via Escape
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(app.current_view, "home")

            # 4. Navigate to SQL Shell
            await pilot.press("2")
            await pilot.pause()
            self.assertEqual(app.current_view, "shell")

            # Test Quick Chip query in Shell
            app.query_one("#chip-sql-tables", Button).press()
            await pilot.pause()
            shell_table = app.query_one("#shell-results-table", DataTable)
            self.assertGreater(shell_table.row_count, 0, "SHOW TABLES should populate rows in Shell")
            self.assertGreater(shell_table.size.height, 0, "Shell table must have visible height > 0")
            self.assertGreater(shell_table.size.width, 0, "Shell table must have visible width > 0")
            self.assertGreater(len(shell_table.columns), 0, "Shell table must have column headers")

            # Test custom query in Shell
            query_input = app.query_one("#shell-query-input", Input)
            query_input.value = "SELECT NORAD_ID, Name FROM Satellite LIMIT 3;"
            app.query_one("#btn-run-query", Button).press()
            await pilot.pause()
            self.assertEqual(shell_table.row_count, 3, "Custom query should return 3 rows")
            self.assertEqual(len(shell_table.columns), 2, "Custom query should have 2 columns")
            self.assertGreater(shell_table.size.height, 0, "Shell table must remain visible")

            # Return Home
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(app.current_view, "home")

            # 5. Navigate to Settings
            await pilot.press("3")
            await pilot.pause()
            self.assertEqual(app.current_view, "settings")

            # Click Save Settings
            app.query_one("#btn-save-settings", Button).press()
            await pilot.pause()

            # Return Home
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(app.current_view, "home")

            # 6. Test Help Guide Modal
            await pilot.press("question_mark")
            await pilot.pause()
            self.assertEqual(len(app.screen_stack), 2)
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(len(app.screen_stack), 1)

    def test_raw_sql_execution(self) -> None:
        res = execute_raw_sql("SELECT COUNT(*) AS total FROM Satellite")
        self.assertEqual(res["status"], "ok")
        self.assertGreater(res["rows"][0][0], 0)

    def test_offline_multi_api_sync_and_progress(self) -> None:
        steps_recorded = []

        def on_prog(step: int, total: int, msg: str, pct: float) -> None:
            steps_recorded.append((step, msg))

        result = run_multi_source_sync(source="cache", live=False, progress_callback=on_prog)
        self.assertEqual(result["status"], "success")
        self.assertGreater(result["upserted"], 0)
        self.assertGreater(len(steps_recorded), 0)


if __name__ == "__main__":
    unittest.main()
