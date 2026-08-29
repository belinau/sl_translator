"""Plain printed invoice — XLSX template fill + LibreOffice PDF.

Workflow 1: fills ``Račun 2026-template.xlsx`` with invoice data, then
converts to PDF via ``libreoffice --headless --convert-to pdf``.

All output is in Slovenian.  Numbers in the XLSX use Excel euro format
``[$€-2] #,##0.00`` which renders as comma decimals in Slovenian locale.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from openpyxl import load_workbook

import config
from translate_core.invoice_models import InvoiceData

log = logging.getLogger(__name__)

_TEMPLATE_PATH = config.INVOICE_TEMPLATE_DIR / "Račun 2026-template.xlsx"

# Cell map from the template — these are fixed and must not be changed.
# Row 18 = headers, row 19 = service type, row 20 = lang pair, row 21+ = data.
_FIRST_DATA_ROW = 19
_TOTAL_LABEL_ROW = 29


def generate_plain_invoice_xlsx(data: InvoiceData) -> bytes:
    """Fill the XLSX template with invoice data.  Returns raw bytes."""
    wb = load_workbook(_TEMPLATE_PATH)
    ws = wb["Račun"]

    # ── Invoice metadata (D3-E6) ──
    ws["E3"] = data.invoice_number
    ws["E4"] = data.issue_date
    ws["E5"] = data.due_date
    # E6 = service date — show range "DD.MM.YYYY - DD.MM.YYYY"
    ws["E6"] = f"{data.service_date_from:%d.%m.%Y} - {data.service_date_to:%d.%m.%Y}"

    # ── Client block (A11-D14) ──
    ws["A12"] = data.client.name
    ws["A13"] = data.client.address
    ws["A14"] = f"{data.client.postal_code} {data.client.city}"
    ws["D11"] = data.client.vat_id
    ws["D12"] = data.order_number
    ws["D13"] = data.project_code

    # ── Service rows ──
    # Each line item uses 3 rows: service type (row N), lang_pair (row N+1),
    # description + unit + qty + price + formula (row N+2).
    # The template has rows 19-27 available (3 rows × ~2-3 line items).
    # We start at row 19 and work down, but if there are too many items
    # we extend the sheet (clearing any existing content below).
    row = _FIRST_DATA_ROW
    for item in data.line_items:
        # Service type row
        ws.cell(row=row, column=1, value=item.service_type)
        row += 1
        # Lang pair row
        ws.cell(row=row, column=1, value=item.lang_pair)
        row += 1
        # Description + values row
        ws.cell(row=row, column=1, value=item.description)
        ws.cell(row=row, column=2, value=item.unit)
        ws.cell(row=row, column=3, value=float(item.quantity))
        ws.cell(row=row, column=4, value=float(item.unit_price))
        # Value formula: =C{row}*D{row}
        ws.cell(row=row, column=5, value=f"=C{row}*D{row}")
        # Apply formats to the data row
        c = ws.cell(row=row, column=3)
        c.number_format = "0.00"
        ws.cell(row=row, column=4).number_format = "0.00"
        ws.cell(row=row, column=5).number_format = "[$€-2] #,##0.00"
        row += 1

    # ── Total row ──
    last_data_row = row - 1
    total_row = _TOTAL_LABEL_ROW
    # If we extended past the template's row 29, push total down
    if last_data_row >= total_row:
        total_row = last_data_row + 2

    ws.cell(row=total_row, column=4, value="ZA PLAČILO")
    ws.cell(row=total_row, column=5, value=f"=SUM(E{_FIRST_DATA_ROW}:E{last_data_row})")
    ws.cell(row=total_row, column=5).number_format = "[$€-2] #,##0.00"
    # Font for total
    from openpyxl.styles import Font
    ws.cell(row=total_row, column=4).font = Font(name="Avenir Roman", size=10)
    ws.cell(row=total_row, column=5).font = Font(name="Avenir Roman", size=14)

    # ── Legal notes + signature — keep template rows (31-33) ──
    # These are already in the template at rows 31-33.  If we pushed
    # the total down, we need to move them too.
    if total_row != _TOTAL_LABEL_ROW:
        offset = total_row - _TOTAL_LABEL_ROW
        # Read original legal note rows and shift them down
        for orig_row in [31, 32, 33]:
            new_row = orig_row + offset
            for col in range(1, 6):
                src = ws.cell(row=orig_row, column=col)
                dst = ws.cell(row=new_row, column=col, value=src.value)
                if src.font:
                    dst.font = src.font.copy()
                if src.number_format:
                    dst.number_format = src.number_format
            # Clear original
            for col in range(1, 6):
                ws.cell(row=orig_row, column=col, value=None)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate_plain_invoice_pdf(xlsx_bytes: bytes) -> bytes:
    """Convert XLSX bytes to PDF via LibreOffice headless.

    Raises RuntimeError if LibreOffice is not found.
    """
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError(
            "LibreOffice not found. Install with: brew install --cask libreoffice"
        )

    with tempfile.TemporaryDirectory(prefix="invoice_") as tmpdir:
        xlsx_path = Path(tmpdir) / "invoice.xlsx"
        xlsx_path.write_bytes(xlsx_bytes)

        result = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                tmpdir,
                str(xlsx_path),
            ],
            capture_output=True,
            timeout=60,
        )
        pdf_path = Path(tmpdir) / "invoice.pdf"
        if not pdf_path.exists():
            raise RuntimeError(
                f"LibreOffice conversion failed: {result.stderr.decode()[:500]}"
            )
        return pdf_path.read_bytes()