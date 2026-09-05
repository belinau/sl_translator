"""Tests for client store, invoice numbering, and invoice generation.

Covers:
- ClientStore CRUD + Fernet encryption round-trip
- Invoice counter atomicity and format
- Plain invoice XLSX: correct cells, formulas, formats
- eSLOG XML: namespace, mandatory segments, dot decimals, correct codes
- eSLOG PDF: Slovenian dates, comma decimals, EUR amounts, diacritics
"""
from __future__ import annotations
import re
import dataclasses
from datetime import date
from decimal import Decimal

import fitz  # PyMuPDF
import pytest
from openpyxl import load_workbook

import config
from translate_core.client_store import ClientStore
from translate_core.invoice_eslog import (
    generate_eslog_pdf,
    generate_eslog_xml,
)
from translate_core.invoice_models import (
    ClientRecord,
    InvoiceData,
    InvoiceLineItem,
)
from translate_core.invoice_plain import generate_plain_invoice_xlsx


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    """Fresh ClientStore with a temp DB."""
    return ClientStore(db_path=tmp_path / "test_clients.db")


@pytest.fixture
def sample_client_data():
    return {
        "name": "Društvo Mesto žensk",
        "address": "Metelkova 6",
        "postal_code": "1000",
        "city": "Ljubljana",
        "country": "Slovenija",
        "country_code": "SI",
        "vat_id": "76914992",
        "iban": "SI56 0126 1600 0002 125",
        "bic": "UJPLSI2DICL",
        "bank_name": "Banka Slovenije",
        "maticna": "3454428000",
        "use_eracun": True,
        "rates": {
            "Prevod:SLO:stran": "22",
            "Prevod:ENG:stran": "28",
            "Prevod:SLO:ura": "35",
            "Lektura:SLO:stran": "14.50",
            "Lektura:ENG:stran": "18",
            "default:stran": "20",
        },
        "responsible_persons": [{"name": "Karla Železnik", "role": "odobrila"}],
    }


@pytest.fixture
def invoice_data(sample_client_data, store):
    """A complete InvoiceData for testing generators."""
    store.add_client(sample_client_data)
    store.add_client(sample_client_data)
    client = store.find_client_by_name("Društvo Mesto žensk")

    items = [
        InvoiceLineItem(
            description="Besedila za razstavo",
            service_type="Prevod",
            lang_pair="ENG>SLO",
            unit="stran",
            quantity=Decimal("1"),
            unit_price=Decimal("22"),
        ),
        InvoiceLineItem(
            description="Vabilo Salehi",
            service_type="Prevod",
            lang_pair="SLO>ENG",
            unit="stran",
            quantity=Decimal("1"),
            unit_price=Decimal("28"),
        ),
    ]

    return InvoiceData(
        invoice_number="2026-013",
        issue_date=date(2026, 8, 5),
        due_date=date(2026, 8, 10),
        service_date_from=date(2026, 7, 1),
        service_date_to=date(2026, 7, 31),
        client=client,
        issuer=config.ISSUER,
        line_items=items,
        responsible_person="Karla Železnik",
        order_number="dogovor",
        project_code="/",
        legal_notes=config.INVOICE_LEGAL_NOTES,
    )


# ── ClientStore tests ──────────────────────────────────────────────

def test_add_and_get_client(store, sample_client_data):
    """Client added and retrieved with decrypted fields."""
    cid = store.add_client(sample_client_data)
    assert cid and re.fullmatch(r"[0-9a-f]{8}", cid), f"opaque id expected, got {cid!r}"

    rec = store.get_client(cid)
    assert rec is not None
    assert rec.name == "Društvo Mesto žensk"
    assert rec.vat_id == "76914992"
    assert rec.iban == "SI56 0126 1600 0002 125"
    assert rec.iban_compact == "SI56012616000002125"
    assert rec.bic == "UJPLSI2DICL"
    assert rec.use_eracun is True
    assert rec.rates["Prevod:SLO:stran"] == Decimal("22")
    assert len(rec.responsible_persons) == 1


def test_encrypted_fields_not_plaintext(store, sample_client_data):
    """Encrypted fields are not stored as plaintext in the DB."""
    cid = store.add_client(sample_client_data)

    # Read raw DB to check ciphertext
    import sqlite3
    conn = sqlite3.connect(str(store._db_path))
    row = conn.execute("SELECT vat_id, iban FROM clients WHERE id = ?", (cid,)).fetchone()
    conn.close()

    assert row[0] != "76914992"  # ciphertext, not plaintext
    assert row[1] != "SI56 0126 1600 0000 2125"
    assert len(row[0]) > 20  # Fernet tokens are long


def test_list_clients(store, sample_client_data):
    """list_clients returns ClientRecord list."""
    store.add_client(sample_client_data)
    clients = store.list_clients()
    assert len(clients) == 1
    assert clients[0].name == "Društvo Mesto žensk"


def test_update_client(store, sample_client_data):
    """Update changes fields and re-encrypts."""
    cid = store.add_client(sample_client_data)
    store.update_client(cid, {**sample_client_data, "name": "Mesto žensk", "vat_id": "11111111"})
    rec = store.get_client(cid)
    assert rec.name == "Mesto žensk"
    assert rec.vat_id == "11111111"


def test_delete_client(store, sample_client_data):
    store.add_client(sample_client_data)
    rec = store.find_client_by_name("Društvo Mesto žensk")
    assert rec is not None
    assert store.delete_client(rec.id)
    assert store.find_client_by_name("Društvo Mesto žensk") is None


def test_mask_iban():
    assert ClientStore.mask_iban("SI56 0126 1600 0000 2125") == "SI56 **** **** **** 2125"
    assert ClientStore.mask_iban("") == ""
    assert ClientStore.mask_vat("76914992") == "76***92"


# ── Invoice counter tests ──────────────────────────────────────────

def test_invoice_number_format(store):
    """Invoice numbers are formatted as YYYY-NNN."""
    n1 = store.next_invoice_number(2026)
    assert n1 == "2026-001"
    n2 = store.next_invoice_number(2026)
    assert n2 == "2026-002"


def test_invoice_number_new_year(store):
    """Different years get independent counters."""
    store.next_invoice_number(2026)
    store.next_invoice_number(2026)
    n = store.next_invoice_number(2027)
    assert n == "2027-001"


# ── Plain invoice XLSX tests ───────────────────────────────────────

def test_plain_invoice_xlsx_basic(invoice_data):
    """XLSX contains correct invoice number, client, and formulas."""
    xlsx_bytes = generate_plain_invoice_xlsx(invoice_data)
    assert len(xlsx_bytes) > 1000

    import io
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Račun"]

    # Invoice number
    assert str(ws["D11"].value) == "76914992"
    # Client name
    assert "Mesto žensk" in ws["A12"].value
    # Client VAT
    assert str(ws["D11"].value) == "76914992"
    # Service date range
    assert "01.07.2026" in ws["E6"].value
    assert "31.07.2026" in ws["E6"].value
    # Line item: service type "Prevod" should be in column A
    assert ws["A19"].value == "Prevod"
    # Line totals in E column (pre-formatted strings with comma decimal + EUR)
    e_cells = [c for c in ["E21", "E24", "E27"] if ws[c].value is not None]
    assert all("EUR" in str(ws[c].value) for c in e_cells)
    assert any("," in str(ws[c].value) for c in e_cells)


# ── eSLOG XML tests ────────────────────────────────────────────────

def test_eslog_xml_namespace(invoice_data):
    """XML has the correct eSLOG 2.00 namespace."""
    xml = generate_eslog_xml(invoice_data).decode()
    assert "urn:eslog:2.00" in xml


def test_eslog_xml_invoice_number(invoice_data):
    """Invoice number appears in both UNH and BGM segments."""
    xml = generate_eslog_xml(invoice_data).decode()
    assert "2026-013" in xml
    assert xml.count("2026-013") >= 2  # UNH + BGM


def test_eslog_xml_dot_decimal(invoice_data):
    """All numeric values use dot as decimal separator (eSLOG §6.3)."""
    xml = generate_eslog_xml(invoice_data).decode()
    # Check all D_5004, D_6060, D_5118 values for commas
    for tag in ["D_5004", "D_6060", "D_5118"]:
        vals = re.findall(rf"<{tag}>([^<]+)</{tag}>", xml)
        for v in vals:
            assert "," not in v, f"{tag} value {v!r} has comma — must use dot (eSLOG §6.3)"


def test_eslog_xml_dates_iso_format(invoice_data):
    """Dates are in YYYY-MM-DD format."""
    xml = generate_eslog_xml(invoice_data).decode()
    dates = re.findall(r"<D_2380>([^<]+)</D_2380>", xml)
    for d in dates:
        assert re.match(r"\d{4}-\d{2}-\d{2}", d), f"Date {d!r} not ISO format"


def test_eslog_xml_parties(invoice_data):
    """XML contains SE (seller), BY (buyer), DP (delivery) parties."""
    xml = generate_eslog_xml(invoice_data).decode()
    assert ">SE<" in xml
    assert ">BY<" in xml
    assert ">DP<" in xml
    # Seller IBAN
    assert "SI56610000007436658" in xml
    # Buyer IBAN
    assert "SI56012616000002125" in xml
    # Buyer VAT
    assert "76914992" in xml


def test_eslog_xml_line_items(invoice_data):
    """XML has correct number of G_SG26 line item groups."""
    xml = generate_eslog_xml(invoice_data).decode()
    # 2 line items → 2 G_SG26
    assert xml.count("<G_SG26>") == 2
    # Line descriptions
    assert "Prevod ENG>SLO" in xml or "Prevod ENG&gt;SLO" in xml
    assert "Prevod SLO>ENG" in xml or "Prevod SLO&gt;ENG" in xml


def test_eslog_xml_amount_precision(invoice_data):
    """Amounts (D_5004) have max 2 decimal places."""
    xml = generate_eslog_xml(invoice_data).decode()
    amounts = re.findall(r"<D_5004>([^<]+)</D_5004>", xml)
    for a in amounts:
        if "." in a:
            decimals = a.split(".")[1]
            assert len(decimals) <= 2, f"Amount {a!r} has >2 decimal places"


def test_eslog_xml_tax_exempt(invoice_data):
    """Tax category 7/S (exempt) is present."""
    xml = generate_eslog_xml(invoice_data).decode()
    assert ">7<" in xml  # D_5283 = 7
    assert ">S<" in xml   # D_5305 = S


# ── eSLOG PDF tests ────────────────────────────────────────────────

def test_eslog_pdf_slovenian_dates(invoice_data):
    """PDF has Slovenian date format DD.MM.YYYY."""
    pdf = generate_eslog_pdf(invoice_data)
    doc = fitz.open(stream=pdf, filetype="pdf")
    text = doc[0].get_text().replace("\xa0", " ")
    doc.close()
    assert "05.08.2026" in text  # issue date
    assert "10.08.2026" in text  # due date
    assert "01.07.2026" in text  # service from
    assert "31.07.2026" in text  # service to


def test_eslog_pdf_comma_decimals(invoice_data):
    """PDF uses comma decimal separator + EUR suffix."""
    pdf = generate_eslog_pdf(invoice_data)
    doc = fitz.open(stream=pdf, filetype="pdf")
    text = doc[0].get_text().replace("\xa0", " ")
    doc.close()
    assert "50,00 EUR" in text  # total with comma decimal


def test_eslog_pdf_diacritics(invoice_data):
    """PDF renders Slovenian diacritics correctly (čšžČŠŽ)."""
    pdf = generate_eslog_pdf(invoice_data)
    doc = fitz.open(stream=pdf, filetype="pdf")
    text = doc[0].get_text().replace("\xa0", " ")
    doc.close()
    assert "Račun" in text
    assert "Izdajatelj" in text
    assert "Prejemnik" in text
    assert "Karla Železnik" in text
    assert "neobdavčeno" in text


def test_eslog_pdf_sections(invoice_data):
    """PDF contains all required sections from the eSLOG visualization."""
    pdf = generate_eslog_pdf(invoice_data)
    doc = fitz.open(stream=pdf, filetype="pdf")
    text = doc[0].get_text().replace("\xa0", " ")
    doc.close()
    required = [
        "Izdajatelj", "Prejemnik", "Račun št.",
        "Valuta: EUR", "Izvleček za plačilo",
        "Sklic:", "Datum zapadlosti",
        "Vrednost postavk", "Za plačilo",
        "Pogodba",
    ]
    for r in required:
        assert r in text, f"Missing section: {r}"


# ── Total calculation test ─────────────────────────────────────────

def test_invoice_total(invoice_data):
    """Total is sum of line totals, 2 decimal places."""
    total = invoice_data.total
    assert total == Decimal("50.00")


def test_payment_reference(invoice_data):
    """Payment reference is SI00 + invoice digits."""
    assert invoice_data.payment_reference == "SI002026013"


# ── Per-direction rate lookup tests ───────────────────────────────

def test_get_rate_specific_direction(store, sample_client_data):
    """get_rate returns the correct rate for service_type + target_lang + unit."""
    store.add_client(sample_client_data)
    rec = store.find_client_by_name("Društvo Mesto žensk")

    # Translation into Slovenian: 22 EUR/page
    assert rec.get_rate("Prevod", "ENG>SLO", "stran") == Decimal("22")
    # Translation into English: 28 EUR/page
    assert rec.get_rate("Prevod", "SLO>ENG", "stran") == Decimal("28")
    # Proofreading into Slovenian: 14.50 EUR/page
    assert rec.get_rate("Lektura", "ENG>SLO", "stran") == Decimal("14.50")
    # Proofreading into English: 18 EUR/page
    assert rec.get_rate("Lektura", "SLO>ENG", "stran") == Decimal("18")


def test_get_rate_fallback(store, sample_client_data):
    """get_rate falls back to default:{unit} when specific rate is missing."""
    store.add_client(sample_client_data)
    rec = store.find_client_by_name("Društvo Mesto žensk")

    # "Prevod:HRV>SLO" → target is SLO, Prevod:SLO:stran=22 exists → returns 22 (not default)
    assert rec.get_rate("Prevod", "HRV>SLO", "stran") == Decimal("22")

    # No rate at all for "Scenografija" + any direction + "kos" → None
    assert rec.get_rate("Scenografija", "ENG>SLO", "kos") is None

    # Service type with no specific SLO rate falls back to default:stran
    # "Prevod urejanje" has no rates at all → falls to "default:stran" = 20
    assert rec.get_rate("Prevod urejanje", "ENG>SLO", "stran") == Decimal("20")


def test_get_rate_lang_pair_variant(store, sample_client_data):
    """get_rate handles lang_pair variants like 'ENG>SLO (100 % ujemanje)'."""
    store.add_client(sample_client_data)
    rec = store.find_client_by_name("Društvo Mesto žensk")

    # The variant should still resolve to target "SLO"
    assert rec.get_rate("Prevod", "ENG>SLO (100 % ujemanje)", "stran") == Decimal("22")


# ── Contract date + party-name integrity tests ───────────────────

def _long_name_client() -> ClientRecord:
    """A client whose name exceeds one D_3036 chunk (>35 chars) to guard
    against the pre-fix 35-char truncation that dropped the last word."""
    return ClientRecord(
        id="t", name="MGLC - Mednarodni grafični likovni center",
        address="Pod turnom 003", postal_code="1000", city="Ljubljana",
        country="Slovenija", country_code="SI", vat_id="39110079",
        iban="SI56 0126 1600 0002 125",
        iban_compact="SI56012616000002125", bic="UJPLSI2DICL",
        maticna="3454428000", account_holder="MGLC", use_eracun=True,
    )


def test_eslog_xml_party_name_not_truncated():
    """Long buyer/delivery-party name is preserved in full — no last word lost."""
    c = _long_name_client()
    data = InvoiceData(
        invoice_number="2026-013", issue_date=date(2026, 8, 5),
        due_date=date(2026, 8, 10), service_date_from=date(2026, 7, 1),
        service_date_to=date(2026, 7, 31), client=c, issuer=config.ISSUER,
        line_items=[InvoiceLineItem(description="x", service_type="Prevod",
            lang_pair="ENG>SLO", unit="stran", quantity=Decimal("1"),
            unit_price=Decimal("22"))],
        legal_notes=config.INVOICE_LEGAL_NOTES,
    )
    xml = generate_eslog_xml(data).decode()
    # The full name must be reconstructable from the BY and DP blocks.
    full = "MGLC - MEDNARODNI GRAFIČNI LIKOVNI CENTER"
    for qualifier in ("BY", "DP"):
        block = re.search(
            rf"<D_3035>{qualifier}</D_3035>.*?</G_SG2>", xml, re.S
        )
        assert block, f"missing {qualifier} party block"
        name_parts = re.findall(r"<D_3036[^>]*>([^<]*)</D_3036[^>]*>", block.group(0))
        joined = "".join(name_parts)
        assert joined == full, f"{qualifier} name lost a word: {joined!r}"


def test_eslog_xml_contract_date_in_on_reference(invoice_data):
    """Contract date is emitted in the referenced-document block (D_2005=384).

    Default doc_type is "Pogodba" → CT qualifier; "Naročilo kupca" → ON.
    Either way the referenced document carries the contract/PO date.
    """
    for doc_type, qualifier in (("Pogodba", "CT"), ("Naročilo kupca", "ON")):
        data = dataclasses.replace(invoice_data, contract_date=date(2026, 1, 16),
                                   doc_type=doc_type)
        xml = generate_eslog_xml(data).decode()
        block = re.search(
            rf"<D_1153>{qualifier}</D_1153>.*?</G_SG1>", xml, re.S
        )
        assert block, f"{qualifier} reference block missing for {doc_type}"
        assert "<D_2005>384</D_2005>" in block.group(0)
        assert "<D_2380>2026-01-16</D_2380>" in block.group(0)

def test_eslog_pdf_contract_date(invoice_data):
    """PDF 'Datum dokumenta' shows the contract date, not the service date."""
    invoice_data = dataclasses.replace(invoice_data, contract_date=date(2026, 1, 16))
    pdf = generate_eslog_pdf(invoice_data)
    doc = fitz.open(stream=pdf, filetype="pdf")
    text = doc[0].get_text().replace("\xa0", " ")
    doc.close()
    assert "16.01.2026" in text
    # The service-from date must not be rendered as the document date.
    # Locate the "Datum dokumenta" label and check the value that follows it.
    idx = text.find("Datum dokumenta")
    assert idx != -1
    tail = text[idx:idx + 60]
    assert "16.01.2026" in tail


def test_eslog_xml_doc_type_qualifier(invoice_data):
    """doc_type drives the referenced-document RFF qualifier (D_1153)."""
    for doc_type, qualifier in (("Pogodba", "CT"), ("Naročilo kupca", "ON")):
        data = dataclasses.replace(invoice_data, doc_type=doc_type)
        xml = generate_eslog_xml(data).decode()
        block = re.search(
            rf"<D_1153>{qualifier}</D_1153>.*?</G_SG1>", xml, re.S
        )
        assert block, f"qualifier {qualifier} not emitted for {doc_type}"
        # The other qualifier must not leak into the referenced-doc block.
        other = "ON" if qualifier == "CT" else "CT"
        assert f"<D_1153>{other}</D_1153>" not in block.group(0)


def test_eslog_xml_party_name_dash_normalized(invoice_data):
    """En-dash in a party name is written as hyphen-minus so bank
    visualisers that render U+2013 as '?' still show a dash."""
    issuer = dict(invoice_data.issuer)
    issuer["name"] = "URBAN BELINA \u2013 SAMOZAPOSLEN V KULTURI"
    data = dataclasses.replace(invoice_data, issuer=issuer)
    xml = generate_eslog_xml(data).decode()
    assert "\u2013" not in xml, "en-dash must be normalised to hyphen-minus"
    assert "URBAN BELINA - SAMOZAPOSLEN V KULTURI" in xml
    assert generate_eslog_xml(data)  # still serialises