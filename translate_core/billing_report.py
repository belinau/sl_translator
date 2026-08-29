"""Project volume report generation — PDF (PyMuPDF) and XLSX (openpyxl).

Both exporters take a list of project billing dicts with keys:
    filename, lang_pair, chars_with, chars_without, pages, rate, total

All output is in Slovenian.  The issuer header (Urban Belina –
samozaposlen v kulturi) appears at the top of both formats for visual
consistency with the invoice documents.

PDF uses Arial TrueType for Slovenian diacritics, comma decimals, and
"EUR" for euro amounts.  XLSX uses Calibri with the euro number format.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import fitz  # PyMuPDF
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side

import config

_FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"
_FN = "arial"


def _format_lang_pair(pair: str) -> str:
    """'en->sl' → 'EN → SL', handles any 'src->tgt' format."""
    if not pair or "->" not in pair:
        return pair or "—"
    src, tgt = pair.split("->", 1)
    return f"{src.strip().upper()} → {tgt.strip().upper()}"


def _sl_float(val: float) -> str:
    """Format a float with comma decimal separator (Slovenian)."""
    return f"{val:.2f}".replace(".", ",")


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
            "chars_with": p.get("chars_with", 0),
            "chars_without": p.get("chars_without", 0),
            "pages": pages,
            "rate": rate,
            "total": total,
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
    margin_t = 50
    col_widths = [160, 80, 75, 75, 55, 70]  # filename, direction, chars_w, chars_no, pages, total
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

    def _new_page():
        nonlocal page, y
        page = doc.new_page(width=595, height=842)
        page.insert_font(fontname=_FN, fontfile=_FONT_PATH)
        y = margin_t

    # ── Issuer header block ──
    _text(margin_l, y, iss["name"], sz=14, color=(0.07, 0.49, 0.37))
    y += 16
    # Split profession line — it has a newline
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
    headers = ["Dokument", "Smer", "Znaki s presl.",
               "Znaki brez", "Strani", "Skupaj (EUR)"]
    for i, h in enumerate(headers):
        _text(col_x[i] + 3, y + 13, h, sz=8, color=(1, 1, 1))
    y += header_h

    # ── Rows ──
    row_h = 18
    for idx, r in enumerate(rows):
        if y + row_h > 800:  # page break
            _new_page()
            page.draw_rect(fitz.Rect(margin_l, y, table_right, y + header_h),
                           color=(0.07, 0.49, 0.37), fill=(0.07, 0.49, 0.37))
            for i, h in enumerate(headers):
                _text(col_x[i] + 3, y + 13, h, sz=8, color=(1, 1, 1))
            y += header_h

        bg = (0.93, 0.96, 0.94) if idx % 2 == 0 else (1, 1, 1)
        page.draw_rect(fitz.Rect(margin_l, y, table_right, y + row_h),
                       color=None, fill=bg)

        fname = r["filename"]
        if len(fname) > 28:
            fname = fname[:25] + "…"
        _text(col_x[0] + 3, y + 12, fname, sz=8)

        _text(col_x[1] + 3, y + 12, r["direction"], sz=8, color=(0.3, 0.3, 0.3))

        _text(col_x[2] + 3, y + 12, f"{r['chars_with']:,}", sz=8)

        _text(col_x[3] + 3, y + 12, f"{r['chars_without']:,}", sz=8)

        _text(col_x[4] + 3, y + 12, _sl_float(r["pages"]), sz=8)

        total_str = f"{_sl_float(r['total'])} EUR"
        _text_right(col_x[5] + col_widths[5] - 3, y + 12, total_str, sz=9,
                    color=(0.07, 0.49, 0.37))

        y += row_h

    # ── Grand total ──
    y += 6
    page.draw_line(fitz.Point(margin_l, y), fitz.Point(table_right, y),
                   color=(0.07, 0.49, 0.37), width=1.5)
    y += 16

    grand_pages = sum(r["pages"] for r in rows)
    grand_total = sum(r["total"] for r in rows)

    _text(col_x[4] + 3, y, "Skupaj strani:", sz=10)
    _text(margin_l, y, f"Projektov: {len(rows)}", sz=9, color=(0.5, 0.5, 0.5))

    gt_str = f"{_sl_float(grand_total)} EUR"
    _text_right(col_x[5] + col_widths[5] - 3, y, gt_str, sz=14,
                color=(0.07, 0.49, 0.37))

    y += 8
    _text(col_x[4] + 3, y, _sl_float(grand_pages), sz=9, color=(0.3, 0.3, 0.3))

    # ── Footer note ──
    y += 30
    _text(margin_l, y,
          "Strani = znaki brez presledkov ÷ 1500.  Skupaj = strani × cena.",
          sz=7, color=(0.6, 0.6, 0.6))
    y += 10
    _text(margin_l, y,
          f"Cena na stran in skupne vrednosti so po projektu ({now}).",
          sz=7, color=(0.6, 0.6, 0.6))

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

    # ── Issuer header (rows 1-3) ──
    ws.merge_cells("A1:H1")
    ws["A1"] = iss["name"]
    ws["A1"].font = Font(name="Calibri", size=14, bold=True, color="22C55E")
    ws["A1"].alignment = Alignment(horizontal="center")

    ws.merge_cells("A2:H2")
    ws["A2"] = iss["profession"].replace("\n", " ").strip()
    ws["A2"].font = Font(name="Calibri", size=9, color="666666")
    ws["A2"].alignment = Alignment(horizontal="center")

    # ── Report title (row 3) ──
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    ws.merge_cells("A3:H3")
    ws["A3"] = f"Pregled obsega projekta  ·  Generirano: {now}  ·  Projektov: {len(rows)}"
    ws["A3"].font = Font(name="Calibri", size=9, color="666666")
    ws["A3"].alignment = Alignment(horizontal="center")

    # ── Header row (row 5) ──
    header_row = 5
    headers = ["#", "Dokument", "Smer", "Znaki s presledki",
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
        ws.cell(row=row_num, column=2, value=r["filename"]).alignment = left_align
        ws.cell(row=row_num, column=3, value=r["direction"]).alignment = center_align
        ws.cell(row=row_num, column=4, value=r["chars_with"]).alignment = right_align
        ws.cell(row=row_num, column=5, value=r["chars_without"]).alignment = right_align
        # Pages
        pages_cell = ws.cell(row=row_num, column=6, value=float(r["pages"]))
        pages_cell.alignment = right_align
        pages_cell.number_format = "0.00"
        # Rate
        rate_cell = ws.cell(row=row_num, column=7, value=float(r["rate"]))
        rate_cell.alignment = right_align
        rate_cell.number_format = "0.00"
        # Total
        total_cell = ws.cell(row=row_num, column=8, value=float(r["total"]))
        total_cell.alignment = right_align
        total_cell.number_format = "#,##0.00"
        # Borders
        for col in range(1, 9):
            ws.cell(row=row_num, column=col).border = thin_border

    # ── Grand total row ──
    total_row = data_start + len(rows)
    grand_pages = sum(r["pages"] for r in rows)
    grand_total = sum(r["total"] for r in rows)
    for col in range(1, 9):
        ws.cell(row=total_row, column=col).fill = total_fill
        ws.cell(row=total_row, column=col).border = Border(
            top=Side(style="medium", color="22C55E"),
            bottom=Side(style="medium", color="22C55E"),
        )
    ws.cell(row=total_row, column=2, value="SKUPAJ").font = total_font
    # Sum pages
    gp_cell = ws.cell(row=total_row, column=6, value=float(grand_pages))
    gp_cell.number_format = "0.00"
    gp_cell.font = total_font
    gp_cell.alignment = right_align
    # Sum total
    gt_cell = ws.cell(row=total_row, column=8, value=float(grand_total))
    gt_cell.number_format = "#,##0.00"
    gt_cell.font = total_font
    gt_cell.alignment = right_align

    # ── Column widths ──
    widths = [5, 45, 14, 18, 18, 16, 14, 16]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + col)].width = w

    # ── Note row ──
    note_row = total_row + 2
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=8)
    ws.cell(row=note_row, column=1,
            value="Strani = znaki brez presledkov ÷ 1500.  Skupaj = strani × cena.  "
                  "Vrednosti so izračunane ob izvozu.")
    ws.cell(row=note_row, column=1).font = Font(name="Calibri", size=8, color="999999", italic=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()