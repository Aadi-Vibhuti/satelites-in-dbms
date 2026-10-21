"""Terminal UI styling, Unicode box-drawing, and table formatters for OIS."""

from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date, datetime
from typing import Any

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# ANSI Color codes
CLR_RESET = "\033[0m"
CLR_BOLD = "\033[1m"
CLR_DIM = "\033[2m\033[38;5;244m"

CLR_CYAN = "\033[38;5;51m"
CLR_CYAN_BOLD = "\033[1;38;5;51m"
CLR_BLUE = "\033[38;5;39m"
CLR_BLUE_BOLD = "\033[1;38;5;39m"
CLR_GREEN = "\033[38;5;48m"
CLR_GREEN_BOLD = "\033[1;38;5;48m"
CLR_YELLOW = "\033[38;5;220m"
CLR_YELLOW_BOLD = "\033[1;38;5;220m"
CLR_RED = "\033[38;5;203m"
CLR_RED_BOLD = "\033[1;38;5;203m"
CLR_MAGENTA = "\033[38;5;213m"
CLR_BORDER = "\033[38;5;240m"
CLR_HEADER = "\033[1;38;5;81m"


def is_color_enabled() -> bool:
    return sys.stdout.isatty()


def color(text: str, color_code: str) -> str:
    if not is_color_enabled():
        return text
    return f"{color_code}{text}{CLR_RESET}"


def char_width(ch: str) -> int:
    if ch in ("\ufe0e", "\ufe0f"):
        return 0
    w = unicodedata.east_asian_width(ch)
    if w in ("W", "F"):
        return 2
    cp = ord(ch)
    if (0x1F300 <= cp <= 0x1FAFF) or (0x2600 <= cp <= 0x27BF):
        return 2
    return 1


def visible_len(s: str) -> int:
    s_clean = ANSI_RE.sub("", str(s))
    return sum(char_width(ch) for ch in s_clean)


def pad(s: str, width: int, align: str = "left") -> str:
    vlen = visible_len(s)
    diff = max(0, width - vlen)
    if align == "right":
        return " " * diff + s
    if align == "center":
        left = diff // 2
        right = diff - left
        return " " * left + s + " " * right
    return s + " " * diff


def format_value(value: Any) -> str:
    if value is None:
        return color("-", CLR_DIM)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def format_status(status: Any) -> str:
    s = str(status).lower() if status else ""
    if s == "active":
        return color("● active", CLR_GREEN)
    if s == "inactive":
        return color("○ inactive", CLR_YELLOW)
    if s == "decayed":
        return color("✕ decayed", CLR_RED)
    if s == "unknown":
        return color("? unknown", CLR_DIM)
    if s == "tracked":
        return color("● tracked", CLR_CYAN)
    if s == "success":
        return color("✔ success", CLR_GREEN)
    if s == "failed":
        return color("✕ failed", CLR_RED)
    if s == "partial":
        return color("▲ partial", CLR_YELLOW)
    return format_value(status)


def format_alert_status(status: Any) -> str:
    s = str(status).lower() if status else ""
    if s == "open":
        return color("■ open", CLR_RED_BOLD)
    if s == "watch":
        return color("▲ watch", CLR_YELLOW_BOLD)
    if s == "cleared":
        return color("✔ cleared", CLR_GREEN)
    if s == "expired":
        return color("⊘ expired", CLR_DIM)
    return format_value(status)


def format_probability(prob: Any) -> str:
    try:
        p = float(prob)
        p_str = f"{p:.2f}%"
        if p >= 90.0:
            return color(p_str, CLR_RED_BOLD)
        if p >= 70.0:
            return color(p_str, CLR_YELLOW_BOLD)
        return color(p_str, CLR_GREEN)
    except (ValueError, TypeError):
        return format_value(prob)


def format_cell(key: str, val: Any) -> str:
    val_str = str(val).lower() if val is not None else ""
    if val_str in ("open", "watch", "cleared", "expired"):
        return format_alert_status(val)
    if val_str in ("active", "inactive", "decayed", "tracked", "success", "failed", "partial"):
        return format_status(val)
    k_lower = key.lower()
    if k_lower in ("status", "tracking status", "conjunction_status"):
        return format_status(val)
    if k_lower in ("probability", "p %", "prob", "p_%", "collision prob"):
        return format_probability(val)
    return format_value(val)


def print_box_table(
    rows: list[dict],
    columns: list[tuple[str, str]] | None = None,
    title: str | None = None,
    indent: int = 2,
) -> None:
    pad_str = " " * indent
    b = lambda t: color(t, CLR_BORDER)
    h = lambda t: color(t, CLR_HEADER)

    if title:
        print(pad_str + color(f"▸ {title}", CLR_CYAN_BOLD))

    if not rows:
        top = pad_str + b("╭" + "─" * 42 + "╮")
        mid = pad_str + b("│") + "  (no records found)                      " + b("│")
        bot = pad_str + b("╰" + "─" * 42 + "╯")
        print(top)
        print(mid)
        print(bot)
        return

    if columns is None:
        keys = list(rows[0].keys())
        columns = [(k, k.replace("_", " ").title()) for k in keys]

    widths: list[int] = []
    aligns: list[str] = []

    for key, header in columns:
        w = visible_len(header)
        is_num = True
        for r in rows:
            val = r.get(key)
            formatted = format_cell(key, val)
            w = max(w, visible_len(formatted))
            if val is not None and not isinstance(val, (int, float)):
                is_num = False
        widths.append(max(w, 4))
        aligns.append("right" if is_num else "left")

    top_parts = [b("─" * (w + 2)) for w in widths]
    top_border = pad_str + b("╭") + b("┬").join(top_parts) + b("╮")

    header_cells = [
        " " + pad(h(hdr), w, align=align) + " "
        for (_, hdr), w, align in zip(columns, widths, aligns)
    ]
    header_line = pad_str + b("│") + b("│").join(header_cells) + b("│")

    div_parts = [b("─" * (w + 2)) for w in widths]
    divider_line = pad_str + b("├") + b("┼").join(div_parts) + b("┤")

    bot_parts = [b("─" * (w + 2)) for w in widths]
    bottom_border = pad_str + b("╰") + b("┴").join(bot_parts) + b("╯")

    print(top_border)
    print(header_line)
    print(divider_line)

    for row in rows:
        row_cells = []
        for (k, _), w, align in zip(columns, widths, aligns):
            raw_val = row.get(k)
            formatted = format_cell(k, raw_val)
            row_cells.append(" " + pad(formatted, w, align=align) + " ")
        print(pad_str + b("│") + b("│").join(row_cells) + b("│"))

    print(bottom_border)


def print_telemetry_card(sat: dict, indent: int = 2) -> None:
    pad_str = " " * indent
    b = lambda t: color(t, CLR_BORDER)
    c_lbl = lambda t: color(t, CLR_DIM)
    c_val = lambda t: color(t, CLR_BOLD)

    card_width = 74
    inner_width = card_width - 2

    def card_line(left_lbl: str, left_val: str, right_lbl: str = "", right_val: str = "") -> str:
        left_combined = f"{c_lbl(left_lbl)}: {left_val}"
        right_combined = f"{c_lbl(right_lbl)}: {right_val}" if right_lbl else ""

        half = (inner_width - 4) // 2
        col1 = pad(left_combined, half)
        col2 = pad(right_combined, half) if right_combined else ""

        line_content = f"  {col1}  {col2}"
        return pad_str + b("│") + pad(line_content, inner_width) + b("│")

    def divider(tag_text: str, color_code: str) -> str:
        dashes = inner_width - visible_len(tag_text) - 1
        return pad_str + b("├─") + color(tag_text, color_code) + b("─" * max(0, dashes)) + b("┤")

    tag_top = " [#] SPACECRAFT TELEMETRY CARD "
    top_dashes = inner_width - visible_len(tag_top) - 1
    top = pad_str + b("╭─") + color(tag_top, CLR_CYAN_BOLD) + b("─" * max(0, top_dashes)) + b("╮")
    print()
    print(top)

    sat_type_badge = color(f"[{sat.get('Sat_Type', 'untyped').upper()}]", CLR_MAGENTA)
    print(card_line("Name", c_val(str(sat.get("Name", "-"))), "NORAD ID", color(str(sat.get("NORAD_ID", "-")), CLR_CYAN_BOLD)))
    print(card_line("Operator", str(sat.get("Operator") or "-"), "Type", sat_type_badge))
    print(card_line("Status", format_status(sat.get("Status")), "Launch Date", format_value(sat.get("Launch_Date"))))

    print(divider(" [o] ORBITAL ELEMENTS ", CLR_BLUE_BOLD))

    inc = sat.get("Inclination")
    inc_str = f"{inc:.4f}°" if inc is not None else "-"
    ecc = sat.get("Eccentricity")
    ecc_str = f"{ecc:.6f}" if ecc is not None else "-"
    apogee = sat.get("Apogee_km")
    apo_str = f"{apogee:,.2f} km" if apogee is not None else "-"
    perigee = sat.get("Perigee_km")
    peri_str = f"{perigee:,.2f} km" if perigee is not None else "-"
    epoch_str = format_value(sat.get("Epoch"))

    print(card_line("Inclination", inc_str, "Eccentricity", ecc_str))
    print(card_line("Apogee Alt", apo_str, "Perigee Alt", peri_str))
    print(card_line("Epoch Ref", epoch_str))

    sat_type = sat.get("Sat_Type")
    if sat_type in ("comm", "nav", "eo", "sci"):
        print(divider(f" [*] PAYLOAD SPECIFICATIONS ({sat_type.upper()}) ", CLR_YELLOW_BOLD))
        if sat_type == "comm":
            print(card_line("Comm Band", str(sat.get("Band") or "-"), "Transponders", str(sat.get("Transponders") or "-")))
        elif sat_type == "nav":
            print(card_line("Constellation", str(sat.get("Constellation") or "-"), "Signal Type", str(sat.get("Signal_Type") or "-")))
        elif sat_type == "eo":
            res = sat.get("Resolution_m")
            res_str = f"{res} m" if res is not None else "-"
            print(card_line("Sensor", str(sat.get("Sensor") or "-"), "Resolution", res_str))
        elif sat_type == "sci":
            print(card_line("Mission", str(sat.get("Mission") or "-"), "Instrument", str(sat.get("Instrument") or "-")))

    bot = pad_str + b("╰" + "─" * inner_width + "╯")
    print(bot)
