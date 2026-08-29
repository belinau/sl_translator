"""eSLOG 2.0 electronic invoice — XML + visualization PDF.

Workflow 2: generates a compliant eSLOG 2.00 XML (``urn:eslog:2.00``
namespace) wrapping UN/EDIFACT INVOIC D.01B, plus a Slovenian-language
A4 visualization PDF matching the standard e-račun rendering.

**XML rules** (eSLOG 2.0 §6.3):
  - Decimal separator: dot (.) only.
  - Amounts (D_5004): max 2 decimal places.
  - Prices (D_5118): max 6 decimal places.
  - Dates (D_2380): YYYY-MM-DD.

**PDF rules** (Slovenian convention):
  - Decimal separator: comma (,).
  - Dates: DD.MM.YYYY.
  - Euro: "EUR" suffix.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from decimal import Decimal
from xml.dom import minidom

import fitz  # PyMuPDF

import config
from translate_core.invoice_models import InvoiceData

log = logging.getLogger(__name__)

_NS = "urn:eslog:2.00"


# ── XML helpers ──────────────────────────────────────────────────

def _fmt_amount(val: Decimal) -> str:
    """Format amount for XML: dot decimal, max 2 places."""
    return f"{val.quantize(Decimal('0.01'))}"


def _fmt_price(val: Decimal) -> str:
    """Format unit price for XML: dot decimal, max 6 places."""
    return f"{val.quantize(Decimal('0.000001'))}"


def _fmt_date(d) -> str:
    """Format date for XML: YYYY-MM-DD."""
    return d.strftime("%Y-%m-%d")


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    """Create a namespaced sub-element."""
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def _comp(parent: ET.Element, tag: str) -> ET.Element:
    """Create a namespaced composite element (no text)."""
    return ET.SubElement(parent, tag)


# ── XML generation ───────────────────────────────────────────────

def generate_eslog_xml(data: InvoiceData) -> bytes:
    """Generate eSLOG 2.00 compliant XML.  Returns UTF-8 bytes."""
    # Register namespace to get clean output
    ET.register_namespace("", _NS)

    root = ET.Element("Invoice")
    root.set("xmlns", _NS)
    root.set(
        "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation",
        f"{_NS} eSLOG20_INVOIC_v200.xsd",
    )
    invoic = _sub(root, "M_INVOIC")
    invoic.set("Id", "data")

    # ── S_UNH — message header ──
    unh = _sub(invoic, "S_UNH")
    _sub(unh, "D_0062", data.invoice_number)
    s009 = _comp(unh, "C_S009")
    _sub(s009, "D_0065", "INVOIC")
    _sub(s009, "D_0052", "D")
    _sub(s009, "D_0054", "01B")
    _sub(s009, "D_0051", "UN")

    # ── S_BGM — beginning of message ──
    bgm = _sub(invoic, "S_BGM")
    c002 = _comp(bgm, "C_C002")
    _sub(c002, "D_1001", "380")  # commercial invoice
    c106 = _comp(bgm, "C_C106")
    _sub(c106, "D_1004", data.invoice_number)

    # ── S_DTM — dates ──
    # 137 = issue date
    dtm = _sub(invoic, "S_DTM")
    c507 = _comp(dtm, "C_C507")
    _sub(c507, "D_2005", "137")
    _sub(c507, "D_2380", _fmt_date(data.issue_date))

    # 35 = delivery/service date (use service_date_from)
    dtm2 = _sub(invoic, "S_DTM")
    c507b = _comp(dtm2, "C_C507")
    _sub(c507b, "D_2005", "35")
    _sub(c507b, "D_2380", _fmt_date(data.service_date_from))

    # ── S_FTX — free text ──
    # GEN — generic note (period)
    ftx = _sub(invoic, "S_FTX")
    _sub(ftx, "D_4451", "GEN")
    c108 = _comp(ftx, "C_C108")
    _sub(c108, "D_4440", ".")

    # AGM — tax exemption
    ftx2 = _sub(invoic, "S_FTX")
    _sub(ftx2, "D_4451", "AGM")
    c108b = _comp(ftx2, "C_C108")
    _sub(c108b, "D_4440", config.ESLOG_AGM_TEXT)

    # REG — registration info
    ftx3 = _sub(invoic, "S_FTX")
    _sub(ftx3, "D_4451", "REG")
    c108c = _comp(ftx3, "C_C108")
    _sub(c108c, "D_4440", config.ESLOG_REG_TEXT)

    # ALQ — purpose of service
    ftx4 = _sub(invoic, "S_FTX")
    _sub(ftx4, "D_4451", "ALQ")
    c108d = _comp(ftx4, "C_C108")
    _sub(c108d, "D_4440", "SCVE")

    # ── G_SG1 — references ──
    # PQ — payment reference
    sg1 = _sub(invoic, "G_SG1")
    rff = _sub(sg1, "S_RFF")
    c506 = _comp(rff, "C_C506")
    _sub(c506, "D_1153", "PQ")
    _sub(c506, "D_1154", data.payment_reference)

    # ON — order number + date
    sg1b = _sub(invoic, "G_SG1")
    rffb = _sub(sg1b, "S_RFF")
    c506b = _comp(rffb, "C_C506")
    _sub(c506b, "D_1153", "ON")
    _sub(c506b, "D_1154", data.order_number)
    if data.service_date_from:
        dtm_on = _sub(sg1b, "S_DTM")
        c507_on = _comp(dtm_on, "C_C507")
        _sub(c507_on, "D_2005", "384")
        _sub(c507_on, "D_2380", _fmt_date(data.service_date_from))

    # ── G_SG2 — parties ──
    _build_seller(invoic, data)
    _build_buyer(invoic, data)
    _build_delivery_party(invoic, data)

    # ── G_SG7 — currency ──
    sg7 = _sub(invoic, "G_SG7")
    cux = _sub(sg7, "S_CUX")
    c504 = _comp(cux, "C_C504")
    _sub(c504, "D_6347", "2")
    _sub(c504, "D_6345", "EUR")

    # ── G_SG8 — payment terms + due date ──
    sg8 = _sub(invoic, "G_SG8")
    _sub(sg8, "S_PAT").find("D_4279") if False else _sub(_sub(sg8, "S_PAT"), "D_4279", "1")
    dtm_due = _sub(sg8, "S_DTM")
    c507_due = _comp(dtm_due, "C_C507")
    _sub(c507_due, "D_2005", "13")
    _sub(c507_due, "D_2380", _fmt_date(data.due_date))

    # ── G_SG26 — line items ──
    for i, item in enumerate(data.line_items, 1):
        _build_line_item(invoic, i, item, data)

    # ── G_SG50 — document-level monetary summaries ──
    total = data.total
    _build_doc_amount(invoic, "79", total)    # total excl. VAT
    _build_doc_amount(invoic, "260", Decimal("0"))   # discount
    _build_doc_amount(invoic, "389", Decimal("0"))  # taxable basis
    _build_doc_amount(invoic, "176", Decimal("0"))  # VAT basis
    _build_doc_amount(invoic, "388", total)   # total payable
    _build_doc_amount(invoic, "259", Decimal("0"))  # prepaid
    _build_doc_amount(invoic, "113", Decimal("0"))  # rounding
    _build_doc_amount(invoic, "366", Decimal("0"))  # VAT total
    _build_doc_amount(invoic, "9", total)     # total incl. VAT (exempt → same)

    # ── G_SG52 — tax details ──
    # All 0 for exempt status (ZDoh-2).  Must include all VAT categories
    # to match the standard visualization template.
    for rate_val in [22, Decimal("9.5"), 5, 0]:
        _build_tax_detail(invoic, rate_val)

    # Last tax group — no rate (exempt)
    _build_tax_detail_exempt(invoic)

    # ── Pretty-print ──
    rough = ET.tostring(root, encoding="unicode")
    dom = minidom.parseString(rough)
    pretty = dom.toprettyxml(indent="  ", encoding="UTF-8")
    # Remove blank lines for cleaner output
    lines = [l for l in pretty.decode().split("\n") if l.strip()]
    return "\n".join(lines).encode()


def _build_seller(invoic: ET.Element, data: InvoiceData) -> None:
    """G_SG2 SE — seller (issuer)."""
    iss = data.issuer
    sg2 = _sub(invoic, "G_SG2")
    nad = _sub(sg2, "S_NAD")
    _sub(nad, "D_3035", "SE")
    c080 = _comp(nad, "C_C080")
    # Name may exceed 35 chars — split across D_3036 fields
    name = iss["name"].upper()
    _sub(c080, "D_3036", name[:35])
    if len(name) > 35:
        _sub(c080, "D_3036_2", name[35:70])

    c059 = _comp(nad, "C_C059")
    _sub(c059, "D_3042", iss["address"].upper())
    _sub(nad, "D_3164", iss["city"].upper())
    c819 = _comp(nad, "C_C819")
    _sub(c819, "D_3228", iss["country"].upper())
    _sub(nad, "D_3251", iss["postal"])
    _sub(nad, "D_3207", iss["country_code"])

    # Bank info
    fii = _sub(sg2, "S_FII")
    _sub(fii, "D_3035", "RB")
    c078 = _comp(fii, "C_C078")
    _sub(c078, "D_3194", iss["iban_compact"])
    _sub(c078, "D_3192", iss["bank_name_xml"])

    # Tax references.
    # VA  = VAT registration number (BT-31) — ONLY for VAT-registered entities.
    # AHP = tax registration number (BT-32) — plain davčna št., NO SI prefix,
    #       used when the issuer is NOT in the VAT register (normirani stroški).
    if iss.get("vat_obliged"):
        sg3a = _sub(sg2, "G_SG3")
        rff_a = _sub(sg3a, "S_RFF")
        c506a = _comp(rff_a, "C_C506")
        _sub(c506a, "D_1153", "VA")
        _sub(c506a, "D_1154", f"SI{iss['vat_id']}")

    sg3c = _sub(sg2, "G_SG3")
    rff_c = _sub(sg3c, "S_RFF")
    c506c = _comp(rff_c, "C_C506")
    _sub(c506c, "D_1153", "AHP")
    _sub(c506c, "D_1154", iss["vat_id"])

    # Matična številka
    sg3b = _sub(sg2, "G_SG3")
    rff_b = _sub(sg3b, "S_RFF")
    c506b = _comp(rff_b, "C_C506")
    _sub(c506b, "D_1153", "0199")
    _sub(c506b, "D_1154", iss["maticna"])


def _build_buyer(invoic: ET.Element, data: InvoiceData) -> None:
    """G_SG2 BY — buyer (client).  IBAN required."""
    c = data.client
    sg2 = _sub(invoic, "G_SG2")
    nad = _sub(sg2, "S_NAD")
    _sub(nad, "D_3035", "BY")
    c080 = _comp(nad, "C_C080")
    name = c.name.upper()[:35]
    _sub(c080, "D_3036", name)
    if len(c.name) > 35:
        _sub(c080, "D_3036_2", c.name.upper()[35:70])

    c059 = _comp(nad, "C_C059")
    _sub(c059, "D_3042", c.address.upper())
    _sub(nad, "D_3164", c.city.upper())
    c819 = _comp(nad, "C_C819")
    _sub(c819, "D_3228", c.country.upper())
    _sub(nad, "D_3251", c.postal_code)
    _sub(nad, "D_3207", c.country_code)

    # Buyer bank info (required for e-račun)
    if c.iban_compact:
        fii = _sub(sg2, "S_FII")
        _sub(fii, "D_3035", "BB")
        c078 = _comp(fii, "C_C078")
        _sub(c078, "D_3194", c.iban_compact)
        holder = (c.account_holder or c.name).upper()[:35]
        _sub(c078, "D_3192", holder)
        if c.bic:
            c088 = _comp(fii, "C_C088")
            _sub(c088, "D_3433", c.bic)

    _build_party_tax_refs(sg2, c)


def _build_party_tax_refs(sg2: ET.Element, c) -> None:
    """G_SG3 tax references for a buyer/delivery party.

    VAT-registered  → VA = SI{vat}  (buyer VAT identifier, BT-48)
    Not VAT-registered → AHP = {vat}  (buyer tax registration, NBT-013)
    Matična (0199) whenever present.
    """
    if c.vat_obliged:
        sg3a = _sub(sg2, "G_SG3")
        rff_a = _sub(sg3a, "S_RFF")
        c506a = _comp(rff_a, "C_C506")
        _sub(c506a, "D_1153", "VA")
        _sub(c506a, "D_1154", f"SI{c.vat_id}")
    elif c.vat_id:
        sg3a = _sub(sg2, "G_SG3")
        rff_a = _sub(sg3a, "S_RFF")
        c506a = _comp(rff_a, "C_C506")
        _sub(c506a, "D_1153", "AHP")
        _sub(c506a, "D_1154", c.vat_id)

    if getattr(c, "maticna", ""):
        sg3b = _sub(sg2, "G_SG3")
        rff_b = _sub(sg3b, "S_RFF")
        c506b = _comp(rff_b, "C_C506")
        _sub(c506b, "D_1153", "0199")
        _sub(c506b, "D_1154", c.maticna)


def _build_delivery_party(invoic: ET.Element, data: InvoiceData) -> None:
    """G_SG2 DP — delivery party (same as buyer, no bank details)."""
    c = data.client
    sg2 = _sub(invoic, "G_SG2")
    nad = _sub(sg2, "S_NAD")
    _sub(nad, "D_3035", "DP")
    c080 = _comp(nad, "C_C080")
    _sub(c080, "D_3036", c.name.upper()[:35])

    c059 = _comp(nad, "C_C059")
    _sub(c059, "D_3042", c.address.upper())
    _sub(nad, "D_3164", c.city.upper())
    c819 = _comp(nad, "C_C819")
    _sub(c819, "D_3228", c.country.upper())
    _sub(nad, "D_3251", c.postal_code)
    _sub(nad, "D_3207", c.country_code)

    _build_party_tax_refs(sg2, c)


def _build_line_item(
    invoic: ET.Element,
    line_num: int,
    item,
    data: InvoiceData,
) -> None:
    """G_SG26 — one invoice line."""
    sg26 = _sub(invoic, "G_SG26")
    lin = _sub(sg26, "S_LIN")
    _sub(lin, "D_1082", str(line_num))

    # Description: service_type + lang_pair + description
    imd = _sub(sg26, "S_IMD")
    _sub(imd, "D_7077", "F")
    c273 = _comp(imd, "C_C273")
    desc = f"{item.service_type} {item.lang_pair} {item.description}".strip()
    _sub(c273, "D_7008", desc[:512])

    # Quantity
    qty = _sub(sg26, "S_QTY")
    c186 = _comp(qty, "C_C186")
    _sub(c186, "D_6063", "47")  # quantity
    _sub(c186, "D_6060", str(item.quantity))
    unit_code = config.ESLOG_UNIT_CODES.get(item.unit, "ZP")
    _sub(c186, "D_6411", unit_code)

    # Line amount (MOA 203 = line total, MOA 38 = taxable amount)
    sg27a = _sub(sg26, "G_SG27")
    moa_a = _sub(sg27a, "S_MOA")
    c516a = _comp(moa_a, "C_C516")
    _sub(c516a, "D_5025", "203")
    _sub(c516a, "D_5004", _fmt_amount(item.line_total))

    sg27b = _sub(sg26, "G_SG27")
    moa_b = _sub(sg27b, "S_MOA")
    c516b = _comp(moa_b, "C_C516")
    _sub(c516b, "D_5025", "38")
    _sub(c516b, "D_5004", _fmt_amount(item.line_total))

    # Unit price
    sg29 = _sub(sg26, "G_SG29")
    pri = _sub(sg29, "S_PRI")
    c509 = _comp(pri, "C_C509")
    _sub(c509, "D_5125", "AAA")
    _sub(c509, "D_5118", _fmt_price(item.unit_price))

    # Tax (category 7 = exempt, S = not subject)
    sg34 = _sub(sg26, "G_SG34")
    tax = _sub(sg34, "S_TAX")
    _sub(tax, "D_5283", "7")
    _sub(tax, "D_5305", "S")
    moa_tax1 = _sub(sg34, "S_MOA")
    c516t1 = _comp(moa_tax1, "C_C516")
    _sub(c516t1, "D_5025", "125")
    _sub(c516t1, "D_5004", "0")
    moa_tax2 = _sub(sg34, "S_MOA")
    c516t2 = _comp(moa_tax2, "C_C516")
    _sub(c516t2, "D_5025", "124")

    # Allowance (0%)
    sg39 = _sub(sg26, "G_SG39")
    alc = _sub(sg39, "S_ALC")
    _sub(alc, "D_5463", "A")
    sg41 = _sub(sg39, "G_SG41")
    pcd = _sub(sg41, "S_PCD")
    c501 = _comp(pcd, "C_C501")
    _sub(c501, "D_5245", "1")
    _sub(c501, "D_5482", "0")


def _build_doc_amount(invoic: ET.Element, code: str, val: Decimal) -> None:
    """G_SG50 — document-level monetary amount."""
    sg50 = _sub(invoic, "G_SG50")
    moa = _sub(sg50, "S_MOA")
    c516 = _comp(moa, "C_C516")
    _sub(c516, "D_5025", code)
    _sub(c516, "D_5004", _fmt_amount(val))


def _build_tax_detail(invoic: ET.Element, rate_val: Decimal | int) -> None:
    """G_SG52 — tax details with VAT rate."""
    sg52 = _sub(invoic, "G_SG52")
    tax = _sub(sg52, "S_TAX")
    _sub(tax, "D_5283", "7")
    c241 = _comp(tax, "C_C241")
    _sub(c241, "D_5153", "VAT")
    c243 = _comp(tax, "C_C243")
    _sub(c243, "D_5278", str(rate_val))
    _sub(tax, "D_5305", "S")
    moa1 = _sub(sg52, "S_MOA")
    c516a = _comp(moa1, "C_C516")
    _sub(c516a, "D_5025", "124")
    _sub(c516a, "D_5004", "0")
    moa2 = _sub(sg52, "S_MOA")
    c516b = _comp(moa2, "C_C516")
    _sub(c516b, "D_5025", "125")
    _sub(c516b, "D_5004", "0")


def _build_tax_detail_exempt(invoic: ET.Element) -> None:
    """G_SG52 — exempt tax group (no rate)."""
    sg52 = _sub(invoic, "G_SG52")
    tax = _sub(sg52, "S_TAX")
    _sub(tax, "D_5283", "7")
    moa1 = _sub(sg52, "S_MOA")
    c516a = _comp(moa1, "C_C516")
    _sub(c516a, "D_5025", "124")
    _sub(c516a, "D_5004", "0")
    moa2 = _sub(sg52, "S_MOA")
    c516b = _comp(moa2, "C_C516")
    _sub(c516b, "D_5025", "125")
    _sub(c516b, "D_5004", "0")


# ── Validation ───────────────────────────────────────────────────

def validate_eslog_xml(xml_bytes: bytes) -> tuple[bool, str]:
    """Validate XML against the eSLOG XSD schema.

    Returns (ok, error_message).
    """
    from xmlschema import XMLSchema  # type: ignore[import-not-found]

    try:
        schema = XMLSchema(str(config.ESLOG_XSD_PATH))
    except Exception as e:
        return False, f"Cannot load XSD schema: {e}"

    try:
        # schema.validate raises on failure; is_valid is the boolean API.
        if schema.is_valid(xml_bytes):
            return True, ""
        errors = list(schema.iter_errors(xml_bytes))
        msgs = [f"{e.path}: {e.reason}" for e in errors[:5]]
        return False, "\n".join(msgs) or "Schema validation failed (no details)"
    except Exception as e:
        return False, f"Validation error: {e}"


# ── Visualization PDF ─────────────────────────────────────────────

# Slovenian date / number formatting
def _sl_date(d) -> str:
    """DD.MM.YYYY"""
    return d.strftime("%d.%m.%Y")


def _sl_amount(val: Decimal) -> str:
    """Slovenian amount: comma decimal, EUR suffix.  e.g. '126,91 EUR'"""
    s = f"{val.quantize(Decimal('0.01'))}"
    return s.replace(".", ",") + " EUR"


def _sl_qty(val: Decimal) -> str:
    """Slovenian quantity: comma decimal."""
    s = f"{val.quantize(Decimal('0.01'))}"
    return s.replace(".", ",")


def _sl_price(val: Decimal) -> str:
    """Slovenian unit price: comma decimal."""
    s = f"{val.quantize(Decimal('0.0000'))}"
    return s.replace(".", ",")


def generate_eslog_pdf(data: InvoiceData) -> bytes:
    """Generate the standard eSLOG visualization PDF (A4).

    Slovenian dates (DD.MM.YYYY), comma decimals, EUR amounts.
    Uses Arial TrueType for Slovenian diacritics (čšžČŠŽ).
    """
    _FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"
    _FN = "arial"
    _font_obj = fitz.Font(fontfile=_FONT_PATH)

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 portrait
    page.insert_font(fontname=_FN, fontfile=_FONT_PATH)
    margin = 40
    page_w = 595 - 2 * margin
    col_left_x = margin
    col_right_x = margin + page_w // 2 + 10
    y = margin

    iss = data.issuer
    c = data.client

    def _text(x, yy, text, sz=8, color=(0.15, 0.15, 0.15)):
        page.insert_text((x, yy), text, fontsize=sz, fontname=_FN, color=color)

    def _new_page():
        nonlocal page, y
        page = doc.new_page(width=595, height=842)
        page.insert_font(fontname=_FN, fontfile=_FONT_PATH)
        y = margin

    # ── Header: Issuer (left) | Recipient (right) ──
    _text(col_left_x, y, "Izdajatelj", sz=9, color=(0.4, 0.4, 0.4))
    _text(col_right_x, y, "Prejemnik", sz=9, color=(0.4, 0.4, 0.4))
    y += 14

    # Issuer block (left)
    issuer_lines = [
        iss["name"],
        iss["address"],
        f"{iss['postal']} {iss['city'].upper()}",
        f"IBAN: {iss['iban']}",
        (
            f"ID za DDV: SI{iss['vat_id']}" if iss.get("vat_obliged")
            else f"Davčna št.: {iss['vat_id']}"
        ),
    ]
    for line in issuer_lines:
        _text(col_left_x, y, line, sz=8)
        y += 11

    # Recipient block (right)
    y_right = margin + 14
    recipient_lines = [c.name, c.address, f"{c.postal_code} {c.city.upper()}"]
    if c.iban:
        recipient_lines.append(f"IBAN: {c.iban}")
    if c.vat_id:
        recipient_lines.append(
            f"ID za DDV: SI{c.vat_id}" if c.vat_obliged
            else f"Davčna št.: {c.vat_id}"
        )
    for line in recipient_lines:
        _text(col_right_x, y_right, line, sz=8)
        y_right += 11

    y = max(y, y_right) + 8

    # ── Invoice metadata ──
    meta_lines = [
        f"Račun št. {data.invoice_number}",
        "Vrsta: račun",
        "Valuta: EUR",
        f"Kraj izdaje: {iss['city'].upper()}",
        f"Datum izdaje: {_sl_date(data.issue_date)}",
    ]
    for line in meta_lines:
        _text(col_left_x, y, line, sz=8, color=(0.2, 0.2, 0.2))
        y += 10
    y += 6

    # ── Payment extraction ──
    _text(col_left_x, y, "Izvleček za plačilo:", sz=8, color=(0.2, 0.2, 0.2))
    y += 10
    pay_lines = [
        f"Nakazilo na: {iss['iban_compact']}",
        f"Znesek: {_sl_amount(data.total)}",
        f"Sklic: {data.payment_reference}",
        f"Datum zapadlosti: {_sl_date(data.due_date)}",
    ]
    for line in pay_lines:
        _text(col_left_x, y, line, sz=8)
        y += 10
    y += 8

    # ── Service period line ──
    period = (
        f"Račun za dobavljeno blago / opravljene storitve "
        f"od {_sl_date(data.service_date_from)} do {_sl_date(data.service_date_to)}"
    )
    _text(col_left_x, y, period, sz=8)
    y += 14

    # ── Line items table ──
    headers = ["#", "Opis postavke", "Kol.", "EM", "Cena za kos",
               "Cena * kol.", "% DDV", "Znesek DDV", "Osnova za DDV", "Znesek z DDV"]
    col_x = [margin, margin + 12, margin + 250, margin + 285, margin + 310,
             margin + 350, margin + 390, margin + 415, margin + 440, margin + 470]

    hdr_y = y
    page.draw_rect(fitz.Rect(margin, hdr_y, 555, hdr_y + 14),
                   fill=(0.9, 0.9, 0.9), color=(0.7, 0.7, 0.7))
    for i, h in enumerate(headers):
        _text(col_x[i], hdr_y + 10, h, sz=6, color=(0.1, 0.1, 0.1))
    y = hdr_y + 14

    row_h = 16
    for idx, item in enumerate(data.line_items, 1):
        if y + row_h > 760:
            _new_page()

        bg = (0.96, 0.96, 0.96) if idx % 2 == 0 else (1, 1, 1)
        page.draw_rect(fitz.Rect(margin, y, 555, y + row_h), color=None, fill=bg)

        vals = [
            str(idx),
            f"{item.service_type} {item.lang_pair} {item.description}"[:70],
            _sl_qty(item.quantity),
            config.ESLOG_UNIT_CODES.get(item.unit, "ZP"),
            _sl_price(item.unit_price),
            _sl_amount(item.line_total),
            "neobdavčeno",
            "0,00",
            "0,00",
            _sl_amount(item.line_total),
        ]
        for i, v in enumerate(vals):
            _text(col_x[i], y + 10, v, sz=6)
        y += row_h

    y += 8

    # ── Tax summary ──
    _text(col_left_x, y, "% DDV    Osnova za DDV    Znesek DDV", sz=7, color=(0.3, 0.3, 0.3))
    y += 10
    for rate_str in ["22,00", "9,50", "5,00", "0,00"]:
        _text(col_left_x, y, f"{rate_str}    0,00          0,00", sz=7, color=(0.3, 0.3, 0.3))
        y += 9
    _text(col_left_x, y, "         0,00          0,00", sz=7, color=(0.3, 0.3, 0.3))
    y += 12

    # ── Totals section ──
    total_label_x = margin + 350
    total_value_x = 555 - 2
    totals = [
        ("Vrednost postavk", data.total),
        ("Popust", Decimal("0")),
        ("Osnova za DDV", Decimal("0")),
        ("Vsota zneskov DDV", Decimal("0")),
        ("Neobdavčeno", Decimal("0")),
        ("Znesek z davki in popusti", data.total),
        ("Zamudne obresti", Decimal("0")),
        ("Predplačilo", Decimal("0")),
        ("Izravnava", Decimal("0")),
        ("Za plačilo", data.total),
    ]
    for label, val in totals:
        _text(total_label_x, y, label, sz=7)
        amt_str = _sl_amount(val)
        tw = _font_obj.text_length(amt_str, fontsize=7)
        _text(total_value_x - tw, y, amt_str, sz=7)
        y += 9
    y += 10

    # ── Responsible person ──
    approver_name = getattr(data, "approver", "") or data.responsible_person
    if approver_name:
        resp = f"Mesečni zbirnik je predhodno pregledala in odobrila {approver_name}."
        _text(col_left_x, y, resp, sz=7, color=(0.2, 0.2, 0.2))
        y += 10
    y += 6

    # ── Legal notes ──
    for note in data.legal_notes or config.INVOICE_LEGAL_NOTES:
        while note:
            chunk = note[:80]
            _text(col_left_x, y, chunk, sz=6, color=(0.3, 0.3, 0.3))
            y += 8
            note = note[80:]
    y += 4

    _text(col_left_x, y,
          "Opomba: e-račun je prikazan v standardni vizualizaciji na podlagi podatkov e-Slog XML datoteki,",
          sz=6, color=(0.3, 0.3, 0.3))
    y += 8
    _text(col_left_x, y,
          "zato se lahko podatki razlikujejo od tistih v PDF prilogi!",
          sz=6, color=(0.3, 0.3, 0.3))
    y += 10

    # ── Reference document ──
    _text(col_left_x, y, "Številka ref. dokumenta", sz=7, color=(0.3, 0.3, 0.3))
    _text(col_left_x + 200, y, data.order_number, sz=7)
    y += 9
    _text(col_left_x, y, "Vrsta dokumenta", sz=7, color=(0.3, 0.3, 0.3))
    _text(col_left_x + 200, y, "Pogodba", sz=7)
    y += 9
    _text(col_left_x, y, "Datum dokumenta", sz=7, color=(0.3, 0.3, 0.3))
    _text(col_left_x + 200, y, _sl_date(data.service_date_from), sz=7)

    return doc.tobytes()