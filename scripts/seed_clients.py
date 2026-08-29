"""Seed the two real clients from the invoice templates.

Run once:  .venv/bin/python scripts/seed_clients.py
Safe to re-run — updates existing clients (matched by name).
"""
from __future__ import annotations

from translate_core.client_store import ClientStore


def main() -> None:
    store = ClientStore()

    # ── Client 1: Društvo Mesto žensk (from XLSX template) ──
    # Plain invoice client — no e-račun, not VAT obliged (društvo,
    # davčna št. written without SI prefix).
    mesto_zensk = {
        "name": "Društvo Mesto žensk",
        "address": "Metelkova 6",
        "postal_code": "1000",
        "city": "Ljubljana",
        "country": "Slovenija",
        "country_code": "SI",
        "vat_id": "76914992",
        "vat_obliged": False,
        "iban": "",
        "bic": "",
        "bank_name": "",
        "maticna": "",
        "account_holder": "",
        "default_order_number": "dogovor",
        "default_project_code": "/",
        "default_doc_type": "Pogodba",
        "default_doc_ref": "",
        "use_eracun": False,
        "rates": {
            "Prevod:SLO:stran": "22",
            "Prevod:ENG:stran": "28",
            "Lektura:SLO:stran": "14.50",
            "Lektura:ENG:stran": "18",
            "default:stran": "20",
        },
        "responsible_persons": [],
    }

    # ── Client 2: MGLC (from eSLOG XML + PDF template) ──
    # e-račun client. VAT 39110079 shown WITHOUT SI prefix in the PDF
    # → not VAT obliged → AHP tax-registration form in eSLOG XML.
    mglc = {
        "name": "MGLC - Mednarodni grafični likovni center",
        "address": "Pod turnom 003",
        "postal_code": "1000",
        "city": "Ljubljana",
        "country": "Slovenija",
        "country_code": "SI",
        "vat_id": "39110079",
        "vat_obliged": False,
        "iban": "SI56 0126 1600 0002 125",
        "bic": "UJPLSI2DICL",
        "bank_name": "Banka Slovenije",
        "maticna": "3454428000",
        "account_holder": "MGLC",
        "default_order_number": "KOM-L-26-0099",
        "default_project_code": "/",
        "default_doc_type": "Pogodba",
        "default_doc_ref": "13/23",
        "use_eracun": True,
        "rates": {
            "Prevod:SLO:stran": "20.87",
            "Prevod:ENG:stran": "28",
            "Lektura:SLO:stran": "14.50",
            "Lektura:ENG:stran": "18",
            "Prevod:SLO:znak": "0.015",
            "Prevod:ENG:znak": "0.02",
            "Prevod:SLO:avtorska pola": "450",
            "Prevod:ENG:avtorska pola": "550",
            "default:stran": "20",
        },
        "responsible_persons": [
            {"name": "Karla Železnik", "role": "odobrila"},
        ],
    }

    for data in [mesto_zensk, mglc]:
        existing = store.find_client_by_name(data["name"])
        if existing:
            store.update_client(existing.id, data)
            print(f"Updated: {existing.id} — {data['name']}")
        else:
            cid = store.add_client(data)
            print(f"Created: {cid} — {data['name']}")

    print(f"\nClients in store: {len(store.list_clients())}")


if __name__ == "__main__":
    main()