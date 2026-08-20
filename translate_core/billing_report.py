"""Billing report generation — PDF (PyMuPDF) and XLSX (openpyxl).

Both exporters take a list of project billing dicts with keys:
    filename, lang_pair, chars_with, chars_without, pages, rate, total

The PDF is a clean invoice-style document. The XLSX has per-project
rows plus a grand total row with formulas.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import fitz  # PyMuPDF
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side


def _format_lang_pair(pair: str) -> str:
    """'en->sl' → 'EN → SL', handles any 'src->tgt' format."""
    if not pair or "->" not in pair:
        return pair or "—"
    src, tgt = pair.split("->", 1)
    return f"{src.strip().upper()} → {tgt.strip().upper()}"


def _prepare_rows(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise project dicts for the report, filling defaults."""
    rows = []
    for p in projects:
        pages = p.get("pages", 0.0) or 0.0
        rate = p.get("rate") or 0.0
        total = round(pages * rate, 2)
        rows.append({
            "filename": p.get("filename", "Untitled"),
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
    """Generate an invoice-style PDF report. Returns raw bytes."""
    rows = _prepare_rows(projects)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 portrait
    margin_l = 40
    margin_t = 50
    col_widths = [160, 80, 75, 75, 55, 70]  # filename, direction, chars_w, chars_no, pages, total
    col_x = [margin_l]
    for w in col_widths:
        col_x.append(col_x[-1] + w)
    table_right = col_x[-1]

    y = margin_t

    # ── Header ──
    page.insert_text((margin_l, y), "Bel Translation Suite",
                     fontname="helv", fontsize=18, color=(0.07, 0.49, 0.37))
    y += 24
    page.insert_text((margin_l, y), "Billing Report",
                     fontname="helv", fontsize=12, color=(0.3, 0.3, 0.3))
    y += 16
    page.insert_text((margin_l, y), f"Generated: {now}",
                     fontname="helv", fontsize=9, color=(0.5, 0.5, 0.5))
    y += 14
    page.insert_text((margin_l, y), f"Projects: {len(rows)}",
                     fontname="helv", fontsize=9, color=(0.5, 0.5, 0.5))
    y += 20

    # ── Table header ──
    header_h = 20
    page.draw_rect(fitz.Rect(margin_l, y, table_right, y + header_h),
                   color=(0.07, 0.49, 0.37), fill=(0.07, 0.49, 0.37))
    headers = ["Document", "Direction", "Chars w/sp", "Chars no sp", "Pages", "Total (€)"]
    for i, h in enumerate(headers):
        page.insert_text((col_x[i] + 3, y + 13), h,
                         fontname="helv", fontsize=8, color=(1, 1, 1))
    y += header_h

    # ── Rows ──
    row_h = 18
    for idx, r in enumerate(rows):
        if y + row_h > 800:  # page break
            page = doc.new_page(width=595, height=842)
            y = margin_t
            # Repeat header
            page.draw_rect(fitz.Rect(margin_l, y, table_right, y + header_h),
                           color=(0.07, 0.49, 0.37), fill=(0.07, 0.49, 0.37))
            for i, h in enumerate(headers):
                page.insert_text((col_x[i] + 3, y + 13), h,
                                 fontname="helv", fontsize=8, color=(1, 1, 1))
            y += header_h

        bg = (0.93, 0.96, 0.94) if idx % 2 == 0 else (1, 1, 1)
        page.draw_rect(fitz.Rect(margin_l, y, table_right, y + row_h),
                       color=None, fill=bg)

        # Filename — truncate if too long
        fname = r["filename"]
        if len(fname) > 28:
            fname = fname[:25] + "…"
        page.insert_text((col_x[0] + 3, y + 12), fname,
                         fontname="helv", fontsize=8, color=(0.15, 0.15, 0.15))

        page.insert_text((col_x[1] + 3, y + 12), r["direction"],
                         fontname="helv", fontsize=8, color=(0.3, 0.3, 0.3))

        page.insert_text((col_x[2] + 3, y + 12), f"{r['chars_with']:,}",
                         fontname="helv", fontsize=8, color=(0.15, 0.15, 0.15))

        page.insert_text((col_x[3] + 3, y + 12), f"{r['chars_without']:,}",
                         fontname="helv", fontsize=8, color=(0.15, 0.15, 0.15))

        page.insert_text((col_x[4] + 3, y + 12), f"{r['pages']:.2f}",
                         fontname="helv", fontsize=8, color=(0.15, 0.15, 0.15))

        total_str = f"€{r['total']:,.2f}"
        tw = fitz.get_text_length(total_str, fontname="helv", fontsize=9)
        page.insert_text((col_x[5] + col_widths[5] - tw - 3, y + 12), total_str,
                         fontname="helv", fontsize=9, color=(0.07, 0.49, 0.37))

        y += row_h

    # ── Grand total ──
    y += 6
    page.draw_line(fitz.Point(margin_l, y), fitz.Point(table_right, y),
                   color=(0.07, 0.49, 0.37), width=1.5)
    y += 16

    grand_pages = sum(r["pages"] for r in rows)
    grand_total = sum(r["total"] for r in rows)

    page.insert_text((col_x[4] + 3, y), "Total pages:",
                     fontname="helv", fontsize=10, color=(0.15, 0.15, 0.15))
    page.insert_text((margin_l, y), f"Projects: {len(rows)}",
                     fontname="helv", fontsize=9, color=(0.5, 0.5, 0.5))

    gt_str = f"€{grand_total:,.2f}"
    tw = fitz.get_text_length(gt_str, fontname="helv", fontsize=14)
    page.insert_text((col_x[5] + col_widths[5] - tw - 3, y), gt_str,
                     fontname="helv", fontsize=14, color=(0.07, 0.49, 0.37))

    y += 8
    page.insert_text((col_x[4] + 3, y), f"{grand_pages:.2f}",
                     fontname="helv", fontsize=9, color=(0.3, 0.3, 0.3))

    # ── Footer note ──
    y += 30
    page.insert_text((margin_l, y),
                     "Pages calculated: chars without spaces ÷ 1500",
                     fontname="helv", fontsize=7, color=(0.6, 0.6, 0.6))
    y += 10
    page.insert_text((margin_l, y),
                     f"Rate per page and totals are per-project as entered at generation time ({now}).",
                     fontname="helv", fontsize=7, color=(0.6, 0.6, 0.6))

    return doc.tobytes()


# ── XLSX ─────────────────────────────────────────────────────────────────

def generate_xlsx(projects: list[dict[str, Any]]) -> bytes:
    """Generate an XLSX spreadsheet with pre-computed values. Returns raw bytes."""
    rows = _prepare_rows(projects)
    wb = Workbook()
    ws = wb.active
    if ws is None:
        ws = wb.create_sheet()
    ws.title = "Billing Report"

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

    # ── Title row ──
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ws.merge_cells("A1:H1")
    ws["A1"] = "Bel Translation Suite — Billing Report"
    ws["A1"].font = Font(name="Calibri", size=14, bold=True, color="22C55E")
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A2:H2")
    ws["A2"] = f"Generated: {now}  ·  Projects: {len(rows)}"
    ws["A2"].font = Font(name="Calibri", size=9, color="666666")
    ws["A2"].alignment = Alignment(horizontal="center")

    # ── Header row (row 4) ──
    header_row = 4
    headers = ["#", "Document", "Direction", "Chars w/ spaces",
               "Chars no spaces", "Pages (÷1500)", "Rate (€/page)", "Total (€)"]
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
        # Pages — pre-computed float so format always shows 2 decimals
        pages_cell = ws.cell(row=row_num, column=6, value=float(r["pages"]))
        pages_cell.alignment = right_align
        pages_cell.number_format = "0.00"
        # Rate — float so €0.00 format renders
        rate_cell = ws.cell(row=row_num, column=7, value=float(r["rate"]))
        rate_cell.alignment = right_align
        rate_cell.number_format = "€0.00"
        # Total — pre-computed float
        total_cell = ws.cell(row=row_num, column=8, value=float(r["total"]))
        total_cell.alignment = right_align
        total_cell.number_format = "€#,##0.00"
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
    ws.cell(row=total_row, column=2, value="GRAND TOTAL").font = total_font
    # Sum pages — pre-computed
    gp_cell = ws.cell(row=total_row, column=6, value=float(grand_pages))
    gp_cell.number_format = "0.00"
    gp_cell.font = total_font
    gp_cell.alignment = right_align
    # Sum total — pre-computed
    gt_cell = ws.cell(row=total_row, column=8, value=float(grand_total))
    gt_cell.number_format = "€#,##0.00"
    gt_cell.font = total_font
    gt_cell.alignment = right_align

    # ── Column widths ──
    widths = [5, 45, 14, 16, 16, 16, 14, 16]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + col)].width = w

    # ── Note row ──
    note_row = total_row + 2
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=8)
    ws.cell(row=note_row, column=1,
            value="Pages = chars without spaces ÷ 1500. Total = pages × rate. "
                  "Values are pre-computed at export time.")
    ws.cell(row=note_row, column=1).font = Font(name="Calibri", size=8, color="999999", italic=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()