"""Billing units: per-unit rate precision and card unit selection.

Covers the two invoicing regressions:

- the workspace autosave payload omits billing fields, so ``save_project``
  must carry client_id / responsible_person / billing_unit over from disk
  instead of wiping them (the "topmost card keeps losing its client" bug —
  the topmost card is the project currently being translated, i.e. the one
  whose workspace autosave fires);
- per-character ("znak") rates need four decimals end to end: ClientStore
  round-trip, rate lookup, and the printed invoice's price column.
"""
from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook

import config
from translate_core.client_store import ClientStore
from translate_core.invoice_models import ClientRecord, InvoiceData, InvoiceLineItem
from translate_core.invoice_plain import generate_plain_invoice_xlsx


# ── fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    """Fresh ClientStore with a temp DB."""
    return ClientStore(db_path=tmp_path / "test_clients.db")


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    """Point main.PROJECTS_DIR at a temp dir so save_project tests never
    touch data/projects."""
    import main

    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    return tmp_path


def _write_project(projects_dir, pid="abcdef12", **extra):
    data = {
        "id": pid,
        "filename": "test_article.docx",
        "lang_pair": "en->sl",
        "active_index": 0,
        "saved_at": "2026-10-13T10:00:00",
        "total": 1,
        "done": 0,
        "segments": [{"source": "a", "target": "", "status": "pending"}],
    }
    data.update(extra)
    path = projects_dir / f"{pid}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _autosave_payload(pid="abcdef12"):
    """The exact shape WorkspaceState._save_after_delay sends — no billing keys."""
    return {
        "project_id": pid,
        "filename": "test_article.docx",
        "lang_pair": "en->sl",
        "pipeline": "academic",
        "project_type": "",
        "active_index": 0,
        "segments": [{"source": "x", "target": "y", "status": "done"}],
        "segments_meta": [],
        "house_style": "maska",
    }


# ── billing-field survival across workspace autosaves ──────────────

def test_workspace_autosave_keeps_billing_fields(projects_dir):
    """save_project must not wipe billing fields patched onto the project
    JSON — they are absent from the autosave payload."""
    import main

    _write_project(
        projects_dir,
        client_id="ef2ac2ce",
        responsible_person="Nina Slepnik",
        billing_unit="znak",
    )
    main.save_project(_autosave_payload())
    saved = json.loads((projects_dir / "abcdef12.json").read_text(encoding="utf-8"))
    assert saved["client_id"] == "ef2ac2ce"
    assert saved["responsible_person"] == "Nina Slepnik"
    assert saved["billing_unit"] == "znak"
    # The autosave itself still lands (segments updated).
    assert saved["segments"][0]["status"] == "done"


def test_save_project_payload_billing_fields_win(projects_dir):
    """Explicit billing keys in the payload override the on-disk values."""
    import main

    _write_project(
        projects_dir,
        client_id="old",
        responsible_person="old",
        billing_unit="stran",
    )
    payload = {
        **_autosave_payload(),
        "client_id": "new",
        "responsible_person": "new",
        "billing_unit": "znak",
    }
    main.save_project(payload)
    saved = json.loads((projects_dir / "abcdef12.json").read_text(encoding="utf-8"))
    assert saved["client_id"] == "new"
    assert saved["responsible_person"] == "new"
    assert saved["billing_unit"] == "znak"


# ── card unit-toggle visibility logic ────────────────────────────────

def _record(rates: dict[str, str]) -> ClientRecord:
    return ClientRecord(
        id="x",
        name="X",
        address="",
        postal_code="",
        city="",
        rates={k: Decimal(v) for k, v in rates.items()},
    )


def test_available_units_page_client_shows_no_alternates():
    from main import _available_billing_units

    rec = _record({"Prevod:SLO:stran": "21.60"})
    assert _available_billing_units(rec, "en->sl") == ["stran"]


def test_available_units_none_without_client():
    from main import _available_billing_units

    assert _available_billing_units(None, "en->sl") == ["stran"]


def test_available_units_include_znak_and_authorial_sheet():
    from main import _available_billing_units

    rec = _record(
        {
            "Prevod:SLO:stran": "21.60",
            "Prevod:SLO:znak": "0.012",
            "Prevod:SLO:avtorska pola": "350",
        }
    )
    assert _available_billing_units(rec, "en->sl") == ["stran", "znak", "avtorska pola"]


def test_billing_qty_per_unit():
    from config import unit_quantity

    chars_with, chars_without = 30_000, 28_500
    assert unit_quantity("stran", chars_with, chars_without) == 19.0  # ÷1500 w/o spaces
    # znak counts ALL characters, spaces included.
    assert unit_quantity("znak", chars_with, chars_without) == 30_000.0
    assert unit_quantity("avtorska pola", chars_with, chars_without) == 1.0  # ÷30000


def test_available_units_respect_direction():
    """A znak rate for the other direction offers no toggle for this project."""
    from main import _available_billing_units

    rec = _record({"Prevod:ENG:znak": "0.02"})
    assert _available_billing_units(rec, "en->sl") == ["stran"]
    assert _available_billing_units(rec, "sl->en") == ["stran", "znak"]


def test_available_units_accept_default_fallback_key():
    from main import _available_billing_units

    rec = _record({"default:znak": "0.01"})
    assert _available_billing_units(rec, "hr->sl") == ["stran", "znak"]



# ── ClientStore rate precision ───────────────────────────────────────

def test_update_client_rates_merges_and_keeps_precision(store):
    store.add_client(
        {
            "name": "MASKA",
            "address": "Metelkova 6",
            "postal_code": "1000",
            "city": "Ljubljana",
            "rates": {"Prevod:SLO:stran": "21.60"},
            "responsible_persons": [],
        }
    )
    rec = store.find_client_by_name("MASKA")
    assert rec is not None
    assert store.update_client_rates(
        rec.id,
        {"Prevod:SLO:znak": "0.012", "Prevod:SLO:avtorska pola": "350"},
    )

    rec = store.get_client(rec.id)
    assert rec is not None
    # The four-decimal per-character rate survives the round-trip exactly…
    assert rec.rates["Prevod:SLO:znak"] == Decimal("0.012")
    # …and pre-existing rates are untouched.
    assert rec.rates["Prevod:SLO:stran"] == Decimal("21.60")
    assert rec.get_rate("Prevod", "en->sl", "znak") == Decimal("0.012")
    assert rec.get_rate("Prevod", "en->sl", "avtorska pola") == Decimal("350")


def test_update_client_rates_missing_client(store):
    assert store.update_client_rates("deadbeef", {"default:znak": "1"}) is False


# ── printed invoice: znak price renders with four decimals ───────────

def test_plain_invoice_prints_znak_price_with_four_decimals(store):
    store.add_client(
        {
            "name": "MASKA",
            "address": "Metelkova 6",
            "postal_code": "1000",
            "city": "Ljubljana",
            "rates": {"Prevod:SLO:znak": "0.012"},
            "responsible_persons": [],
        }
    )
    client = store.find_client_by_name("MASKA")
    assert client is not None

    rate = client.get_rate("Prevod", "en->sl", "znak")
    assert rate == Decimal("0.012")

    data = InvoiceData(
        invoice_number="2026-042",
        issue_date=date(2026, 10, 13),
        due_date=date(2026, 11, 12),
        service_date_from=date(2026, 10, 1),
        service_date_to=date(2026, 10, 13),
        client=client,
        issuer=config.ISSUER,
        line_items=[
            InvoiceLineItem(
                description=" Članek za revijo",
                service_type="Prevod",
                lang_pair="ENG>SLO",
                unit="znak",
                quantity=Decimal("12345"),
                unit_price=rate,
            ),
            InvoiceLineItem(
                description=" Knjiga — prevod",
                service_type="Prevod",
                lang_pair="ENG>SLO",
                unit="avtorska pola",
                quantity=Decimal("1.1545"),
                unit_price=Decimal("350"),
            ),
        ],
        order_number="dogovor",
        project_code="/",
        legal_notes=config.INVOICE_LEGAL_NOTES,
    )
    # znak: 12.345 × 0,012 = 148,14; avtorska pola: 1,1545 × 350 = 404,08
    assert data.total == Decimal("552.22")

    wb = load_workbook(io.BytesIO(generate_plain_invoice_xlsx(data)))
    ws = wb.active
    assert ws is not None
    values = [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]
    assert "znak" in values                      # unit column
    assert "avtorska pola" in values             # unit column
    assert "0,0120" in values                    # znak price keeps four decimals
    assert "12.345,00" in values or "12345,00" in values  # znak qty
    assert "1,1545" in values                    # pola qty keeps four decimals
    assert "350,00" in values                    # pola price, two decimals
    assert "148,14 EUR" in values                # znak line total
    assert "404,08 EUR" in values                # pola line total
    assert "552,22 EUR" in values                # grand total


def test_config_unit_precision_helpers():
    assert config.unit_price_format("znak") == "%.4f"
    assert config.unit_price_format("stran") == "%.2f"
    assert config.unit_price_format("avtorska pola") == "%.2f"
    assert config.unit_qty_format("avtorska pola") == "%.4f"
    assert config.unit_qty_format("znak") == "%.2f"
    assert config.unit_price_step("znak") == 0.001
    assert config.unit_price_step("stran") == 0.5


def test_config_unit_labels():
    assert config.unit_short_label("avtorska pola") == "avt. pola"
    assert config.unit_short_label("znak") == "znak"
    assert config.unit_short_label("stran") == "stran"


def test_billing_report_prices_in_project_unit():
    """The billing report prices each project in its billing_unit, not
    forced into per-page math — a znak project's total is chars × rate,
    not pages × rate."""
    from translate_core.billing_report import _prepare_rows

    rows = _prepare_rows([
        {"filename": "A", "lang_pair": "en->sl", "chars_with": 10_000,
         "chars_without": 9_000, "pages": 6.0, "billing_unit": "znak",
         "rate": 0.012, "responsible_person": ""},
        {"filename": "B", "lang_pair": "en->sl", "chars_with": 30_000,
         "chars_without": 28_500, "pages": 19.0, "billing_unit": "avtorska pola",
         "rate": 350.0, "responsible_person": ""},
        {"filename": "C", "lang_pair": "en->sl", "chars_with": 3_000,
         "chars_without": 2_850, "pages": 1.9, "billing_unit": "stran",
         "rate": 21.6, "responsible_person": ""},
    ])
    # znak: 10.000 × 0,012 = 120,00
    assert rows[0]["unit"] == "znak"
    assert rows[0]["qty"] == 10_000.0
    assert rows[0]["total"] == 120.0
    # avtorska pola: 30.000 ÷ 30.000 = 1 × 350 = 350,00
    assert rows[1]["unit"] == "avtorska pola"
    assert rows[1]["qty"] == 1.0
    assert rows[1]["total"] == 350.0
    # stran: 1,9 × 21,60 = 41,04
    assert rows[2]["unit"] == "stran"
    assert rows[2]["qty"] == 1.9
    assert rows[2]["total"] == 41.04