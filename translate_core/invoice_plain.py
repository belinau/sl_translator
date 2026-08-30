"""Plain printed invoice — XLSX generated from scratch + LibreOffice PDF.

Builds the entire invoice XLSX programmatically (no template file) so row
placement is always correct regardless of item count.  Converts to PDF
via ``libreoffice --headless --convert-to pdf``.

Visual style matches the billing report: Calibri font, green (#22C55E)
header bar with white text, light green (#D1FAE5) total row, thin gray
borders, green issuer name.

All output is in Slovenian.  Numbers use Excel euro format
``[$€-2] #,##0.00`` which renders as comma decimals in Slovenian locale.
Prices use 4 decimal places for per-character units (znak, avtorska pola).
"""
from __future__ import annotations

import io
import logging
import subprocess
import tempfile
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

import config
from translate_core.invoice_models import InvoiceData

log = logging.getLogger(__name__)

# ── Style constants (shared with billing_report.py) ──
_FONT = "Calibri"
_GREEN = "22C55E"
_GREEN_LIGHT = "D1FAE5"
_GREY = "666666"
_GREY_LIGHT = "CCCCCC"
_WHITE = "FFFFFF"

_COL_WIDTHS = {"A": 21.0, "B": 9.35, "C": 14.85, "D": 20.5, "E": 20.0, "F": 19.35, "G": 8.67}
_EUR_FMT = "[$€-2] #,##0.00"

_thin_border = Border(bottom=Side(style="thin", color=_GREY_LIGHT))
_green_border = Border(
    top=Side(style="medium", color=_GREEN),
    bottom=Side(style="medium", color=_GREEN),
)
_header_font = Font(name=_FONT, size=9, bold=True, color=_WHITE)
_header_fill = PatternFill(start_color=_GREEN, end_color=_GREEN, fill_type="solid")
_total_font = Font(name=_FONT, size=12, bold=True, color=_GREEN)
_total_fill = PatternFill(start_color=_GREEN_LIGHT, end_color=_GREEN_LIGHT, fill_type="solid")
_label_font = Font(name=_FONT, size=9, color=_GREY)
_body_font = Font(name=_FONT, size=10)
_body_font_bold = Font(name=_FONT, size=10, bold=True)
_meta_font = Font(name=_FONT, size=10)
_issuer_font = Font(name=_FONT, size=20, bold=True, color=_GREEN)
_profession_font = Font(name=_FONT, size=9, color=_GREY)
_section_font = Font(name=_FONT, size=12, bold=True, color=_GREEN)
_note_font = Font(name=_FONT, size=9, color=_GREY)
_footer_font = Font(name=_FONT, size=8, italic=True, color="999999")
_align_right = Alignment(horizontal="right")
_center = Alignment(horizontal="center")
_wrap_top = Alignment(wrap_text=True, vertical="top")


def generate_plain_invoice_xlsx(data: InvoiceData) -> bytes:
    """Build the invoice XLSX from scratch. Returns raw bytes."""
    wb = Workbook()
    ws = wb.active
    if ws is None:
        ws = wb.create_sheet()
    ws.title = "Račun"
    ws.sheet_view.showGridLines = False

    for col, w in _COL_WIDTHS.items():
        ws.column_dimensions[col].width = w

    iss = data.issuer
    c = data.client

    # ── Row 1: Issuer name (merged A1:E1, green) ──
    ws.merge_cells("A1:E1")
    ws["A1"] = iss["name"]
    ws["A1"].font = _issuer_font
    ws["A1"].alignment = Alignment(vertical="bottom")
    ws.row_dimensions[1].height = 41.25

    # ── Row 2: Profession (merged A2:E2, grey, wrapped) ──
    ws.merge_cells("A2:E2")
    ws["A2"] = iss["profession"].replace("\n", " ").strip()
    ws["A2"].font = _profession_font
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[2].height = 45.75

    # ── Rows 3-10: Left = issuer details, Right = invoice meta + bank ──
    _left_lines = [
        iss["address"],
        f"{iss['postal']} {iss['city']}",
        iss["country"],
        f"Davčna št.: {iss['vat_id']}",
        f"matična številka: {iss['maticna']}",
        "",
        f"primarna e-pošta: {iss['email']}",
        "",
    ]
    _meta_rows = [
        (3, "Račun št.:", data.invoice_number, False),
        (4, "V Ljubljani, dne:", f"{data.issue_date:%d.%m.%Y}", False),
        (5, "Rok plačila:", f"{data.due_date:%d.%m.%Y}", False),
        (6, "Datum opravljene storitve:",
         f"{data.service_date_from:%d.%m.%Y} - {data.service_date_to:%d.%m.%Y}", False),
        (7, "Številka poslovnega TRR:", None, True),
        (8, None, iss["iban"], True),
        (9, "BIC/SWIFT:", iss["bic"], False),
        (10, "Ime banke:", iss["bank_name"], False),
    ]
    for i, (rr, label, val, merge) in enumerate(_meta_rows):
        ws.row_dimensions[rr].height = 23.25 if i in (4, 5) else 18.0
        if _left_lines[i]:
            ws.cell(row=rr, column=1, value=_left_lines[i]).font = _meta_font
        if merge:
            ws.merge_cells(start_row=rr, start_column=4, end_row=rr, end_column=5)
            if label:
                ws.cell(row=rr, column=4, value=label).font = _label_font
            if val:
                ws.cell(row=rr, column=4, value=val).font = _meta_font
        else:
            if label:
                ws.cell(row=rr, column=4, value=label).font = _label_font
            if val is not None and val != "":
                ws.cell(row=rr, column=5, value=val).font = _meta_font

    # ── Rows 11-14: Client (Naročnik) block ──
    if c:
        ws.cell(row=11, column=1, value="Naročnik:").font = _section_font
        ws.cell(row=11, column=3, value="Davčna št. nar.").font = _label_font
        vat_label = f"SI{c.vat_id}" if c.vat_obliged else (c.vat_id or "")
        ws.cell(row=11, column=4, value=vat_label).font = _body_font_bold
        ws.row_dimensions[11].height = 16.5

        ws.merge_cells("A12:B12")
        ws.cell(row=12, column=1, value=c.name).font = _body_font_bold
        ws.cell(row=12, column=1).alignment = _wrap_top
        ws.cell(row=12, column=3, value="Naročilnica št.:").font = _label_font
        ws.merge_cells("D12:E12")
        ws.cell(row=12, column=4, value=data.order_number).font = _body_font
        ws.row_dimensions[12].height = 33.75

        ws.merge_cells("A13:B13")
        ws.cell(row=13, column=1, value=c.address).font = _body_font
        ws.cell(row=13, column=3, value="Koda projekta:").font = _label_font
        ws.merge_cells("D13:E13")
        ws.cell(row=13, column=4, value=data.project_code).font = _body_font
        ws.row_dimensions[13].height = 17.25

        ws.cell(row=14, column=1, value=f"{c.postal_code} {c.city}").font = _body_font
        ws.row_dimensions[14].height = 16.5

    # Rows 15-17: spacer
    ws.row_dimensions[15].height = 16.5
    ws.row_dimensions[16].height = 16.5
    ws.row_dimensions[17].height = 8.0

    # ── Row 18: Column headers (green bar, white text) ──
    headers = ["STORITEV", "ENOTA", "KOLIČINA", "CENA BREZ DDV", "VREDNOST BREZ DDV"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=18, column=col, value=h)
        cell.font = _header_font
        cell.fill = _header_fill
        cell.alignment = _center
        cell.border = _thin_border
    ws.row_dimensions[18].height = 20.0

    # ── Line items: 3 rows per item ──
    row = 19
    for item in data.line_items:
        # Row 1: service type
        ws.cell(row=row, column=1, value=item.service_type).font = _body_font
        ws.row_dimensions[row].height = 14.25
        row += 1
        # Row 2: language pair
        ws.cell(row=row, column=1, value=item.lang_pair).font = Font(
            name=_FONT, size=10, color=_GREY
        )
        ws.row_dimensions[row].height = 14.25
        row += 1
        # Row 3: description + values (with borders)
        desc = item.full_description
        ws.cell(row=row, column=1, value=desc).font = _body_font
        ws.cell(row=row, column=1).alignment = _wrap_top
        ws.cell(row=row, column=1).border = _thin_border
        ws.cell(row=row, column=2, value=item.unit).font = _body_font
        ws.cell(row=row, column=2).border = _thin_border
        ws.cell(row=row, column=2).alignment = _center
        ws.cell(row=row, column=3, value=float(item.quantity)).font = _body_font
        ws.cell(row=row, column=3).border = _thin_border
        ws.cell(row=row, column=3).alignment = _align_right
        ws.cell(row=row, column=4, value=float(item.unit_price)).font = _body_font
        ws.cell(row=row, column=4).border = _thin_border
        ws.cell(row=row, column=4).alignment = _align_right
        ws.cell(row=row, column=5, value=float(item.line_total)).font = _body_font_bold
        ws.cell(row=row, column=5).border = _thin_border
        ws.cell(row=row, column=5).alignment = _align_right
        ws.cell(row=row, column=3).number_format = "0.00"
        _price_fmt = "0.0000" if item.unit in ("znak", "avtorska pola") else "0.00"
        ws.cell(row=row, column=4).number_format = _price_fmt
        ws.cell(row=row, column=5).number_format = _EUR_FMT
        _est_lines = max(1, (len(desc) + 20) // 21)
        ws.row_dimensions[row].height = max(30.0, _est_lines * 15.0 + 5.0)
        row += 1

    # ── Total block (light green fill, green bold text) ──
    row += 1
    ws.row_dimensions[row - 1].height = 8.0
    total_row = row
    for col in range(1, 6):
        ws.cell(row=total_row, column=col).fill = _total_fill
        ws.cell(row=total_row, column=col).border = _green_border
    ws.cell(row=total_row, column=4, value="ZA PLAČILO").font = _total_font
    ws.cell(row=total_row, column=4).alignment = _align_right
    ws.cell(row=total_row, column=5, value=float(data.total))
    ws.cell(row=total_row, column=5).font = _total_font
    ws.cell(row=total_row, column=5).alignment = _align_right
    ws.cell(row=total_row, column=5).number_format = _EUR_FMT
    ws.row_dimensions[total_row].height = 27.0
    row = total_row + 1

    # ── Legal notes ──
    notes = data.legal_notes or config.INVOICE_LEGAL_NOTES
    for i, note in enumerate(notes):
        rr = row + i
        if i == 1:
            ws.merge_cells(start_row=rr, start_column=1, end_row=rr, end_column=5)
            ws.cell(row=rr, column=1, value=note).font = _note_font
            ws.cell(row=rr, column=1).alignment = Alignment(wrap_text=True)
        else:
            ws.cell(row=rr, column=1, value=note).font = _note_font
        ws.row_dimensions[rr].height = 14.25

    # Signature row
    sign_row = row + len(notes)
    ws.cell(row=sign_row, column=4, value="Podpis:").font = _body_font
    ws.row_dimensions[sign_row].height = 14.25
    row = sign_row + 1

    # ── Footer ──
    footer_row = row + 1
    ws.cell(row=footer_row, column=1, value=config.GENERATED_BY).font = _footer_font
    ws.row_dimensions[footer_row].height = 14.25

    # ── Print area + page setup ──
    ws.print_area = f"A1:E{footer_row}"
    ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    if ws.sheet_properties.pageSetUpPr:
        ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = 0.5
    ws.page_margins.right = 0.5
    ws.page_margins.top = 0.5
    ws.page_margins.bottom = 0.5

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate_plain_invoice_pdf(xlsx_bytes: bytes) -> bytes:
    """Convert XLSX bytes to PDF via LibreOffice headless."""
    with tempfile.TemporaryDirectory() as td:
        xlsx_path = Path(td) / "invoice.xlsx"
        xlsx_path.write_bytes(xlsx_bytes)
        try:
            subprocess.run(
                ["soffice", "--headless", "--convert-to", "pdf",
                 "--outdir", td, str(xlsx_path)],
                capture_output=True, timeout=60, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log.error("LibreOffice conversion failed: %s", e)
            raise RuntimeError("PDF conversion failed — is LibreOffice installed?") from e
        pdf_path = Path(td) / "invoice.pdf"
        if not pdf_path.exists():
            raise RuntimeError("LibreOffice produced no PDF output")
        return pdf_path.read_bytes()