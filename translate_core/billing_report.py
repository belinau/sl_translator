"""Project volume report generation — PDF (PyMuPDF) and XLSX (openpyxl).

Slovenian formatting: comma decimals, DD.MM.YYYY dates.
All document names shown in full (no truncation).
Footer: "Generirano z Bel Translation Suite" on every page.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import fitz  # PyMuPDF
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment

import config

_FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"
_FN = "arial"

_FOOTER = config.GENERATED_BY


def _format_lang_pair(pair: str) -> str:
    """'en->sl' → 'EN → SL', handles any 'src->tgt' format."""
    if "->" not in pair and ">" not in pair:
        return pair
    sep = "->" if "->" in pair else ">"
    src, _, tgt = pair.partition(sep)
    return f"{src.strip().upper()} → {tgt.strip().upper()}"


_LANG_FULL = {
    "en": "angleščina", "sl": "slovenščina", "de": "nemščina",
    "fr": "francoščina", "it": "italijanščina", "es": "španščina",
    "hr": "hrvaščina", "ru": "ruščina",
}


def _format_lang_pair_sl(pair: str) -> str:
    """'en->sl' → 'angleščina → slovenščina' (Slovenian full names)."""
    if "->" not in pair and ">" not in pair:
        return pair
    sep = "->" if "->" in pair else ">"
    src, _, tgt = pair.partition(sep)
    return f"{_LANG_FULL.get(src.strip().lower(), src)} → {_LANG_FULL.get(tgt.strip().lower(), tgt)}"


def _sl_float(val: float, decimals: int = 2) -> str:
    """Format a float with comma decimal separator (Slovenian)."""
    return f"{val:.{decimals}f}".replace(".", ",")


def _prepare_rows(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise project dicts for the report, filling defaults."""
    rows = []
    for p in projects:
        pages = round(p.get("pages", 0.0) or 0.0, 2)
        rate = p.get("rate") or 0.0
        total = round(pages * rate, 2)
        rows.append({
            "filename": p.get("filename", "Brez naslova"),
            "direction": _format_lang_pair(p.get("lang_pair", "")),
            "direction_sl": _format_lang_pair_sl(p.get("lang_pair", "")),
            "chars_with": p.get("chars_with", 0),
            "chars_without": p.get("chars_without", 0),
            "pages": pages,
            "rate": rate,
            "total": total,
            "responsible_person": p.get("responsible_person", ""),
        })
    return rows


# ── PDF ─────────────────────────────────────────────────────────────────

def generate_pdf(projects: list[dict[str, Any]]) -> bytes:
    """Generate a project volume report PDF in Slovenian. Returns raw bytes."""
    rows = _prepare_rows(projects)
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    iss = config.ISSUER

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 portrait
    page.insert_font(fontname=_FN, fontfile=_FONT_PATH)
    _font_obj = fitz.Font(fontfile=_FONT_PATH)

    margin_l = 40
    margin_r = 40
    margin_t = 50


    # Column widths: filename, person, direction, chars_w, chars_no, pages, rate, total
    col_widths = [160, 50, 55, 55, 50, 40, 45, 65]
    col_x = [margin_l]
    for w in col_widths:
        col_x.append(col_x[-1] + w)
    table_right = col_x[-1]

    y = margin_t

    def _text(x, yy, text, sz=8, color=(0.15, 0.15, 0.15)):
        page.insert_text((x, yy), text, fontsize=sz, fontname=_FN, color=color)

    def _text_right(x_right, yy, text, sz=8, color=(0.15, 0.15, 0.15)):
        tw = _font_obj.text_length(text, fontsize=sz)
        page.insert_text((x_right - tw, yy), text, fontsize=sz, fontname=_FN, color=color)

    def _wrap_text(x, yy, text, max_w, sz=8, color=(0.15, 0.15, 0.15), line_h=10):
        """Insert text, wrapping at max_w pixels. Returns y after last line."""
        words = text.split(" ")
        lines = []
        cur = ""
        for w in words:
            test = f"{cur} {w}".strip()
            if _font_obj.text_length(test, fontsize=sz) <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        for line in lines:
            _text(x, yy, line, sz=sz, color=color)
            yy += line_h
        return yy

    def _draw_footer(yy):
        """Draw the generation footer at bottom of page."""
        if yy < 800:
            yy = 800
        _text(margin_l, yy, _FOOTER, sz=6, color=(0.5, 0.5, 0.5))

    def _new_page():
        nonlocal page, y
        _draw_footer(y + 20)
        page = doc.new_page(width=595, height=842)
        page.insert_font(fontname=_FN, fontfile=_FONT_PATH)
        y = margin_t

    # ── Issuer header block ──
    _text(margin_l, y, iss["name"], sz=14, color=(0.07, 0.49, 0.37))
    y += 16
    for line in iss["profession"].split("\n"):
        _text(margin_l, y, line.strip(), sz=8, color=(0.4, 0.4, 0.4))
        y += 10
    y += 4

    # ── Report title ──
    _text(margin_l, y, "Pregled obsega projekta", sz=12, color=(0.3, 0.3, 0.3))
    y += 16
    _text(margin_l, y, f"Generirano: {now}", sz=9, color=(0.5, 0.5, 0.5))
    y += 12
    _text(margin_l, y, f"Število projektov: {len(rows)}", sz=9, color=(0.5, 0.5, 0.5))
    y += 20

    # ── Table header ──
    header_h = 20
    page.draw_rect(fitz.Rect(margin_l, y, table_right, y + header_h),
                   color=(0.07, 0.49, 0.37), fill=(0.07, 0.49, 0.37))
    headers = ["Dokument", "Oseba", "Smer", "Znaki s presl.",
               "Znaki brez", "Strani", "Cena/stran", "Skupaj (EUR)"]
    y += header_h

    # ── Rows ──
    row_h = 18
    for idx, r in enumerate(rows):
        # Calculate how many lines the filename needs
        fname = r["filename"]
        fname_max_w = col_widths[0] - 6
        fname_lines_count = 1
        words = fname.split(" ")
        cur = ""
        for w in words:
            test = f"{cur} {w}".strip()
            if _font_obj.text_length(test, fontsize=8) <= fname_max_w:
                cur = test
            else:
                if cur:
                    fname_lines_count += 1
                cur = w
        needed_h = max(row_h, fname_lines_count * 10 + 6)

        if y + needed_h > 790:  # page break
            _new_page()
            page.draw_rect(fitz.Rect(margin_l, y, table_right, y + header_h),
                           color=(0.07, 0.49, 0.37), fill=(0.07, 0.49, 0.37))
            for i, h in enumerate(headers):
                _text(col_x[i] + 3, y + 13, h, sz=8, color=(1, 1, 1))
            y += header_h

        bg = (0.93, 0.96, 0.94) if idx % 2 == 0 else (1, 1, 1)
        page.draw_rect(fitz.Rect(margin_l, y, table_right, y + needed_h),
                       color=None, fill=bg)

        # Full filename (wrapped, no truncation)
        _wrap_text(col_x[0] + 3, y + 12, fname, fname_max_w, sz=8)

        # Responsible person
        person = r["responsible_person"]
        if person:
            _text(col_x[1] + 3, y + 12, person, sz=8, color=(0.3, 0.3, 0.3))

        _text(col_x[2] + 3, y + 12, r["direction"], sz=8, color=(0.3, 0.3, 0.3))

        _text(col_x[3] + 3, y + 12, f"{r['chars_with']:,}", sz=8)

        _text(col_x[4] + 3, y + 12, f"{r['chars_without']:,}", sz=8)

        _text(col_x[5] + 3, y + 12, _sl_float(r["pages"]), sz=8)

        rate_str = _sl_float(r["rate"], 4) if r["rate"] > 0 else "—"
        _text(col_x[6] + 3, y + 12, rate_str, sz=8, color=(0.3, 0.3, 0.3))

        total_str = f"{_sl_float(r['total'])} EUR"
        _text_right(col_x[7] + col_widths[7] - 3, y + 12, total_str, sz=9,
                    color=(0.07, 0.49, 0.37))

    # ── Grand total ──
    y += 6
    page.draw_line(fitz.Point(margin_l, y), fitz.Point(table_right, y),
                   color=(0.07, 0.49, 0.37), width=1.5)
    y += 16

    grand_pages = sum(r["pages"] for r in rows)
    grand_total = sum(r["total"] for r in rows)

    _text(margin_l, y, f"Projektov: {len(rows)}", sz=9, color=(0.5, 0.5, 0.5))
    _text(col_x[3] + 3, y, "Skupaj strani:", sz=10)
    _text(col_x[5] + 3, y, _sl_float(grand_pages), sz=10, color=(0.3, 0.3, 0.3))

    gt_str = f"{_sl_float(grand_total)} EUR"
    _text_right(col_x[7] + col_widths[7] - 3, y, gt_str, sz=14,
                color=(0.07, 0.49, 0.37))

    # ── Footer notes ──
    y += 30
    _text(margin_l, y,
          "Strani = znaki brez presledkov ÷ 1500.  Skupaj = strani × cena.",
          sz=7, color=(0.6, 0.6, 0.6))
    y += 10
    _text(margin_l, y,
          f"Cena na stran in skupne vrednosti so po projektu ({now}).",
          sz=7, color=(0.6, 0.6, 0.6))

    # ── Generation footer ──
    _draw_footer(y + 20)

    return doc.tobytes()


# ── XLSX ─────────────────────────────────────────────────────────────────

def generate_xlsx(projects: list[dict[str, Any]]) -> bytes:
    """Generate an XLSX project volume report in Slovenian. Returns raw bytes."""
    rows = _prepare_rows(projects)
    wb = Workbook()
    ws = wb.active
    if ws is None:
        ws = wb.create_sheet()
    ws.title = "Pregled obsega"

    iss = config.ISSUER

    # ── Styles ──
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="22C55E", end_color="22C55E", fill_type="solid")
    total_font = Font(name="Calibri", size=11, bold=True)
    total_fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
    thin_border = Border(
        bottom=Side(style="thin", color="CCCCCC"),
    )
    right_align = Alignment(horizontal="right")
    left_align = Alignment(horizontal="left")
    center_align = Alignment(horizontal="center")
    wrap_align = Alignment(horizontal="left", wrap_text=True)

    n_cols = 9  # #, Dokument, Oseba, Smer, chars_w, chars_no, pages, rate, total

    # ── Issuer header (rows 1-3) ──
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    ws["A1"] = iss["name"]
    ws["A1"].font = Font(name="Calibri", size=14, bold=True, color="22C55E")
    ws["A1"].alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n_cols)
    ws["A2"] = iss["profession"].replace("\n", " ").strip()
    ws["A2"].font = Font(name="Calibri", size=9, color="666666")
    ws["A2"].alignment = Alignment(horizontal="center")

    # ── Report title (row 3) ──
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=n_cols)
    ws["A3"] = f"Pregled obsega projekta  ·  Generirano: {now}  ·  Projektov: {len(rows)}"
    ws["A3"].font = Font(name="Calibri", size=9, color="666666")
    ws["A3"].alignment = Alignment(horizontal="center")

    # ── Header row (row 5) ──
    header_row = 5
    headers = ["#", "Dokument", "Odgovorna oseba", "Smer", "Znaki s presledki",
               "Znaki brez presledkov", "Strani (÷1500)", "Cena (EUR/stran)", "Skupaj (EUR)"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align
        cell.border = thin_border

    # ── Data rows ──
    data_start = header_row + 1
    for i, r in enumerate(rows):
        row_num = data_start + i
        ws.cell(row=row_num, column=1, value=i + 1).alignment = center_align
        ws.cell(row=row_num, column=2, value=r["filename"]).alignment = wrap_align
        ws.cell(row=row_num, column=3, value=r["responsible_person"]).alignment = left_align
        ws.cell(row=row_num, column=4, value=r["direction"]).alignment = center_align
        ws.cell(row=row_num, column=5, value=r["chars_with"]).alignment = right_align
        ws.cell(row=row_num, column=6, value=r["chars_without"]).alignment = right_align
        # Pages
        pages_cell = ws.cell(row=row_num, column=7, value=float(r["pages"]))
        pages_cell.alignment = right_align
        pages_cell.number_format = "0.00"
        # Rate
        rate_cell = ws.cell(row=row_num, column=8, value=float(r["rate"]))
        rate_cell.alignment = right_align
        rate_cell.number_format = "0.0000"
        # Total
        total_cell = ws.cell(row=row_num, column=9, value=float(r["total"]))
        total_cell.alignment = right_align
        total_cell.number_format = '#,##0.00" EUR"'
        # Borders
        for col in range(1, n_cols + 1):
            ws.cell(row=row_num, column=col).border = thin_border

    # ── Grand total row ──
    total_row = data_start + len(rows)
    grand_pages = sum(r["pages"] for r in rows)
    grand_total = sum(r["total"] for r in rows)
    for col in range(1, n_cols + 1):
        ws.cell(row=total_row, column=col).fill = total_fill
        ws.cell(row=total_row, column=col).border = Border(
            top=Side(style="medium", color="22C55E"),
            bottom=Side(style="medium", color="22C55E"),
        )
    ws.cell(row=total_row, column=2, value="SKUPAJ").font = total_font
    # Sum pages
    gp_cell = ws.cell(row=total_row, column=7, value=float(grand_pages))
    gp_cell.number_format = "0.00"
    gp_cell.font = total_font
    gp_cell.alignment = right_align
    # Sum total
    gt_cell = ws.cell(row=total_row, column=9, value=float(grand_total))
    gt_cell.number_format = '#,##0.00" EUR"'
    gt_cell.font = total_font
    gt_cell.alignment = right_align

    # ── Column widths ──
    widths = [5, 45, 18, 14, 18, 18, 16, 16, 16]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + col)].width = w

    # ── Note row ──
    note_row = total_row + 2
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=n_cols)
    ws.cell(row=note_row, column=1,
            value="Strani = znaki brez presledkov ÷ 1500.  Skupaj = strani × cena.  "
                  "Vrednosti so izračunane ob izvozu.")
    ws.cell(row=note_row, column=1).font = Font(name="Calibri", size=8, color="999999", italic=True)

    # ── Generation footer ──
    footer_row = note_row + 1
    ws.merge_cells(start_row=footer_row, start_column=1, end_row=footer_row, end_column=n_cols)
    ws.cell(row=footer_row, column=1, value=_FOOTER)
    ws.cell(row=footer_row, column=1).font = Font(name="Calibri", size=8, color="999999")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()