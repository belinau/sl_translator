"""Plain printed invoice — XLSX template fill + LibreOffice PDF.

Workflow 1: fills ``Račun 2026-template.xlsx`` with invoice data, then
converts to PDF via ``libreoffice --headless --convert-to pdf``.

All output is in Slovenian.  Numbers in the XLSX use Excel euro format
``[$€-2] #,##0.00`` which renders as comma decimals in Slovenian locale.

Template layout: line items start at row 19 (three rows per item —
service type, language pair, description + values).  The total block
sits at row 29 and the legal notes at rows 31-33 (A32:E32 merged, D33
"Podpis:").  When more than three items push the items past the total
block, the bottom block is shifted down (unmerge → snapshot → clear →
rewrite → re-merge) BEFORE the items are written, so the items can
never overwrite the legal notes.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

import config
from translate_core.invoice_models import InvoiceData

log = logging.getLogger(__name__)

_TEMPLATE_PATH = config.INVOICE_TEMPLATE_DIR / "Račun 2026-template.xlsx"

_FIRST_DATA_ROW = 19   # first line-item row in the template
_TOTAL_ROW = 29        # template total row (ZA PLAČILO + SUM formula)
_LEGAL_NOTE_ROWS = (31, 32, 33)  # A31/A32/A33 notes; A32:E32 merged
_BOTTOM_FIRST = 30     # first movable row in the bottom block
_SIGN_ROW = 33         # D33 "Podpis:"


def generate_plain_invoice_xlsx(data: InvoiceData) -> bytes:
    """Fill the XLSX template with invoice data.  Returns raw bytes."""
    wb = load_workbook(_TEMPLATE_PATH)
    ws: Worksheet = wb["Račun"]

    # ── Invoice metadata (D3-E6) ──
    ws["E3"] = data.invoice_number
    ws["E4"] = data.issue_date
    ws["E5"] = data.due_date
    ws["E6"] = f"{data.service_date_from:%d.%m.%Y} - {data.service_date_to:%d.%m.%Y}"

    # ── Client block (A11-D14) ──
    ws["A12"] = data.client.name
    ws["A13"] = data.client.address
    ws["A14"] = f"{data.client.postal_code} {data.client.city}"
    ws["D11"] = data.client.vat_id
    ws["D12"] = data.order_number
    ws["D13"] = data.project_code

    # ── Compute shift and move the bottom block BEFORE writing items,
    #    so line-item rows can never overwrite the legal notes. ──
    last_data_row = _FIRST_DATA_ROW + 3 * len(data.line_items) - 1
    offset = 0
    if last_data_row >= _TOTAL_ROW - 1:
        offset = (last_data_row + 2) - _TOTAL_ROW
        _shift_bottom_block(ws, offset)
    total_row = _TOTAL_ROW + offset

    # ── Service rows (3 rows per line item, starting at row 19) ──
    row = _FIRST_DATA_ROW
    for item in data.line_items:
        ws.cell(row=row, column=1, value=item.service_type)
        row += 1
        ws.cell(row=row, column=1, value=item.lang_pair)
        row += 1
        ws.cell(row=row, column=1, value=item.description)
        ws.cell(row=row, column=2, value=item.unit)
        ws.cell(row=row, column=3, value=float(item.quantity))
        ws.cell(row=row, column=4, value=float(item.unit_price))
        ws.cell(row=row, column=5, value=f"=C{row}*D{row}")
        ws.cell(row=row, column=3).number_format = "0.00"
        ws.cell(row=row, column=4).number_format = "0.00"
        ws.cell(row=row, column=5).number_format = "[$€-2] #,##0.00"
        row += 1

    # ── Total block at its (possibly shifted) position ──
    ws.cell(row=total_row, column=4, value="ZA PLAČILO")
    ws.cell(row=total_row, column=5, value=f"=SUM(E{_FIRST_DATA_ROW}:E{last_data_row})")
    ws.cell(row=total_row, column=5).number_format = "[$€-2] #,##0.00"
    ws.cell(row=total_row, column=4).font = Font(name="Avenir Roman", size=10)
    ws.cell(row=total_row, column=5).font = Font(name="Avenir Roman", size=14)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _shift_bottom_block(ws: Worksheet, offset: int) -> None:
    """Move the legal notes + signature down by *offset* rows.

    openpyxl MergedCell values are read-only, so every merged range whose
    rows intersect the block is unmerged first, the rows snapshotted
    (values + fonts), cleared, and rewritten at their new position; the
    full-width merge for the long ZDDV note is re-created there.
    """
    if offset <= 0:
        return

    for rng in list(ws.merged_cells.ranges):
        if rng.min_row >= _BOTTOM_FIRST:
            ws.unmerge_cells(str(rng))

    rows = (*_LEGAL_NOTE_ROWS, _SIGN_ROW)
    snap: list[tuple[int, str | None, str | None, Font]] = []
    for r in rows:
        a_val = ws.cell(row=r, column=1).value
        a_val = a_val.text if hasattr(a_val, "text") else a_val
        d_val = ws.cell(row=r, column=4).value
        d_val = d_val.text if hasattr(d_val, "text") else d_val
        a_font = copy(ws.cell(row=r, column=1).font)
        snap.append((r, a_val, d_val, a_font))

    # Clear originals (fully writable after unmerge).  NOTE: openpyxl's
    # cell(value=None) is a no-op — assignment must go through the
    # attribute, not the constructor argument.
    for r in rows:
        for col in range(1, 6):
            ws.cell(row=r, column=col).value = None

    # Write shifted content.
    for src_r, a_val, d_val, a_font in snap:
        new_r = src_r + offset
        if a_val:
            ws.cell(row=new_r, column=1, value=a_val)
            ws.cell(row=new_r, column=1).font = a_font
        if src_r == _SIGN_ROW and d_val:
            ws.cell(row=new_r, column=4, value=d_val)
            ws.cell(row=new_r, column=4).font = a_font
        if src_r == 32:  # re-create the full-width merge for the long note
            ws.merge_cells(start_row=new_r, start_column=1,
                           end_row=new_r, end_column=5)


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
            [soffice, "--headless", "--convert-to", "pdf",
             "--outdir", tmpdir, str(xlsx_path)],
            capture_output=True,
            timeout=120,
        )
        pdf_path = Path(tmpdir) / "invoice.pdf"
        if not pdf_path.exists():
            raise RuntimeError(
                f"LibreOffice conversion failed: {result.stderr.decode()[:500]}"
            )
        return pdf_path.read_bytes()