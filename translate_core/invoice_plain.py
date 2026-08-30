"""Plain printed invoice — XLSX generated from scratch + LibreOffice PDF.

Builds the entire invoice XLSX programmatically (no template file) so row
placement is always correct regardless of item count.  Converts to PDF
via ``libreoffice --headless --convert-to pdf``.

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
from openpyxl.styles import Font, Alignment

import config
from translate_core.invoice_models import InvoiceData

log = logging.getLogger(__name__)

# Layout constants
_COL_WIDTHS = {"A": 21.0, "B": 9.35, "C": 14.85, "D": 20.5, "E": 20.0, "F": 19.35, "G": 8.67}
_FONT = "Arial"
_EUR_FMT = "[$€-2] #,##0.00"
_DATE_FMT = "DD.MM.YYYY"


def generate_plain_invoice_xlsx(data: InvoiceData) -> bytes:
    """Build the invoice XLSX from scratch. Returns raw bytes."""
    wb = Workbook()
    ws = wb.active
    if ws is None:
        ws = wb.create_sheet()
    ws.title = "Račun"
    ws.sheet_view.showGridLines = False

    # Column widths
    for col, w in _COL_WIDTHS.items():
        ws.column_dimensions[col].width = w

    iss = data.issuer
    c = data.client


    # ── Row 1: Issuer name (merged A1:E1) ──
    ws.merge_cells("A1:E1")
    ws["A1"] = iss["name"]
    ws["A1"].font = Font(name=_FONT, size=20, bold=False)
    ws["A1"].alignment = Alignment(vertical="bottom")
    ws.row_dimensions[1].height = 41.25


    # ── Row 2: Profession (merged A2:E2, wrapped) ──
    ws.merge_cells("A2:E2")
    ws["A2"] = iss["profession"].replace("\n", " ").strip()
    ws["A2"].font = Font(name=_FONT, size=11)
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
    _right_labels = [
        ("Račun št.:", data.invoice_number),
        ("V Ljubljani, dne:", data.issue_date),
        ("Rok plačila:", data.due_date),
        ("Datum opravljene storitve:",
         f"{data.service_date_from:%d.%m.%Y} - {data.service_date_to:%d.%m.%Y}"),
        ("Številka poslovnega TRR:", iss["iban"]),
        ("BIC/SWIFT:", iss["bic"]),
        ("Ime banke:", iss["bank_name"]),
        ("", ""),
    ]
    for i in range(8):
        rr = 3 + i
        ws.row_dimensions[rr].height = 18.0 if i not in (4, 7) else 23.25
        # Left column
        if _left_lines[i]:
            ws.cell(row=rr, column=1, value=_left_lines[i]).font = Font(name=_FONT, size=10)
        # Right label (D) + value (E) — always separate, never merge meta rows
        label, val = _right_labels[i]
        if label:
            ws.cell(row=rr, column=4, value=label).font = Font(name=_FONT, size=9)
        if val is not None and val != "":
            cell = ws.cell(row=rr, column=5, value=val)
            cell.font = Font(name=_FONT, size=10)
            if hasattr(val, "strftime"):
                cell.number_format = _DATE_FMT
    # Merge only IBAN rows (7-8) where the value spans D:E
    ws.merge_cells("D7:E7")
    ws.merge_cells("D8:E8")

    # ── Rows 11-14: Client (Naročnik) block ──
    if c:
        ws.cell(row=11, column=1, value="Naročnik:").font = Font(name=_FONT, size=12)
        ws.cell(row=11, column=3, value="Davčna št. nar.").font = Font(name=_FONT, size=10)
        vat_label = f"SI{c.vat_id}" if c.vat_obliged else (c.vat_id or "")
        ws.cell(row=11, column=4, value=vat_label).font = Font(name=_FONT, size=11)
        ws.row_dimensions[11].height = 16.5

        ws.merge_cells("A12:B12")
        ws.cell(row=12, column=1, value=c.name).font = Font(name=_FONT, size=10)
        ws.cell(row=12, column=1).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=12, column=3, value="Naročilnica št.:").font = Font(name=_FONT, size=10)
        ws.merge_cells("D12:E12")
        ws.cell(row=12, column=4, value=data.order_number).font = Font(name=_FONT, size=10)
        ws.row_dimensions[12].height = 33.75

        ws.merge_cells("A13:B13")
        ws.cell(row=13, column=1, value=c.address).font = Font(name=_FONT, size=10)
        ws.cell(row=13, column=3, value="Koda projekta:").font = Font(name=_FONT, size=10)
        ws.merge_cells("D13:E13")
        ws.cell(row=13, column=4, value=data.project_code).font = Font(name=_FONT, size=10)
        ws.row_dimensions[13].height = 17.25

        ws.cell(row=14, column=1, value=f"{c.postal_code} {c.city}").font = Font(name=_FONT, size=10)
        ws.row_dimensions[14].height = 16.5

    # Rows 15-17: spacer
    ws.row_dimensions[15].height = 16.5
    ws.row_dimensions[16].height = 16.5
    ws.row_dimensions[17].height = 8.0

    # ── Row 18: Column headers ──
    headers = ["STORITEV", "ENOTA", "KOLIČINA", "CENA BREZ DDV", "VREDNOST BREZ DDV"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=18, column=col, value=h)
        cell.font = Font(name=_FONT, size=9, bold=False)
    ws.row_dimensions[18].height = 12.75

    # ── Line items: 3 rows per item ──
    row = 19
    first_value_row = row + 2  # 3rd row of first item (where qty/price/total live)
    for item in data.line_items:
        # Row 1: service type
        ws.cell(row=row, column=1, value=item.service_type).font = Font(name=_FONT, size=10)
        ws.row_dimensions[row].height = 14.25
        row += 1
        # Row 2: language pair
        ws.cell(row=row, column=1, value=item.lang_pair).font = Font(name=_FONT, size=10)
        ws.row_dimensions[row].height = 14.25
        row += 1
        # Row 3: description + values
        desc = item.full_description
        ws.cell(row=row, column=1, value=desc).font = Font(name=_FONT, size=10)
        ws.cell(row=row, column=1).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=row, column=2, value=item.unit).font = Font(name=_FONT, size=10)
        ws.cell(row=row, column=3, value=float(item.quantity)).font = Font(name=_FONT, size=10)
        ws.cell(row=row, column=4, value=float(item.unit_price)).font = Font(name=_FONT, size=10)
        ws.cell(row=row, column=5, value=f"=C{row}*D{row}").font = Font(name=_FONT, size=10)
        # Number formats
        ws.cell(row=row, column=3).number_format = "0.00"
        _price_fmt = "0.0000" if item.unit in ("znak", "avtorska pola") else "0.00"
        ws.cell(row=row, column=4).number_format = _price_fmt
        ws.cell(row=row, column=5).number_format = _EUR_FMT
        # Row height: estimate lines based on col A width (~21 chars)
        _est_lines = max(1, (len(desc) + 20) // 21)
        ws.row_dimensions[row].height = max(30.0, _est_lines * 15.0 + 5.0)
        row += 1
    last_value_row = row - 1

    # ── Total block ──
    row += 1  # blank spacer row
    ws.row_dimensions[row - 1].height = 8.0
    total_row = row
    ws.cell(row=total_row, column=4, value="ZA PLAČILO").font = Font(name=_FONT, size=12, bold=True)
    ws.cell(row=total_row, column=5, value=f"=SUM(E{first_value_row}:E{last_value_row})")
    ws.cell(row=total_row, column=5).font = Font(name=_FONT, size=12, bold=True)
    ws.cell(row=total_row, column=5).number_format = _EUR_FMT
    ws.row_dimensions[total_row].height = 27.0
    row = total_row + 1

    # ── Legal notes ──
    notes = data.legal_notes or config.INVOICE_LEGAL_NOTES
    for i, note in enumerate(notes):
        rr = row + i
        if i == 1:  # long note → merge A:E
            ws.merge_cells(start_row=rr, start_column=1, end_row=rr, end_column=5)
            ws.cell(row=rr, column=1, value=note).font = Font(name=_FONT, size=9)
            ws.cell(row=rr, column=1).alignment = Alignment(wrap_text=True)
        else:
            ws.cell(row=rr, column=1, value=note).font = Font(name=_FONT, size=9)
        ws.row_dimensions[rr].height = 14.25

    # Signature row (same as last legal note row or +1)
    sign_row = row + len(notes)
    ws.cell(row=sign_row, column=4, value="Podpis:").font = Font(name=_FONT, size=10)
    ws.row_dimensions[sign_row].height = 14.25
    row = sign_row + 1

    # ── Footer ──
    footer_row = row + 1
    ws.cell(row=footer_row, column=1, value=config.GENERATED_BY).font = Font(
        name=_FONT, size=7, italic=True, color="999999"
    )
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