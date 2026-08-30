"""Shared data structures for invoice generation.

Both plain invoices and eSLOG e-invoices consume these frozen dataclasses.
All amounts are Decimal to avoid float rounding issues.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ClientRecord:
    """A billing client, decrypted from the secure store."""

    id: str
    name: str
    address: str
    postal_code: str
    city: str
    country: str = "Slovenija"
    country_code: str = "SI"
    vat_id: str = ""
    vat_obliged: bool = False
    iban: str = ""
    iban_compact: str = ""
    bic: str = ""
    bank_name: str = ""
    maticna: str = ""
    account_holder: str = ""
    default_order_number: str = "dogovor"
    default_project_code: str = "/"
    default_doc_type: str = "Pogodba"
    default_doc_ref: str = ""
    use_eracun: bool = False
    rates: dict[str, Decimal] = field(default_factory=dict)
    responsible_persons: list[dict[str, str]] = field(default_factory=list)

    def get_rate(self, service_type: str, lang_pair: str, unit: str) -> Decimal | None:
        """Look up the per-unit price for a given service/direction/unit.

        Rate keys are ``"{service_type}:{target_lang}:{unit}"``.
        Falls back to ``"{service_type}:{unit}"`` (no direction) then
        to ``"default:{unit}"`` if the specific key is missing.
        """
        from config import parse_target, rate_key
        target = parse_target(lang_pair)
        # Most specific first
        key = rate_key(service_type, target, unit)
        if key in self.rates:
            return self.rates[key]
        # Fallback: service_type + unit, no target lang
        key2 = f"{service_type}:{unit}"
        if key2 in self.rates:
            return self.rates[key2]
        # Fallback: default + unit
        key3 = f"default:{unit}"
        if key3 in self.rates:
            return self.rates[key3]
        return None

@dataclass(frozen=True)
class InvoiceLineItem:
    """One billable line on an invoice."""

    description: str
    service_type: str       # "Prevod", "Lektura", …  (from INVOICE_SERVICE_TYPES)
    lang_pair: str          # "ENG>SLO", "SLO>ENG", …
    unit: str               # "stran", "ura", "pavšal", …
    quantity: Decimal
    unit_price: Decimal      # per-unit rate in EUR
    responsible_person: str = ""  # surname in brackets at end of description

    @property
    def full_description(self) -> str:
        """Description with responsible person in brackets (if present).

        Avoids doubling if the person is already in the description text
        (e.g. from invoices generated before the responsible_person field).
        """
        if self.responsible_person:
            suffix = f" ({self.responsible_person})"
            if not self.description.endswith(suffix):
                return f"{self.description}{suffix}"
        return self.description

    @property
    def line_total(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class InvoiceData:
    """Everything needed to generate a plain invoice or eSLOG XML."""

    invoice_number: str               # "2026-013"
    issue_date: date
    due_date: date
    service_date_from: date
    service_date_to: date
    client: ClientRecord | None = None
    issuer: dict = field(default_factory=dict)
    line_items: list[InvoiceLineItem] = field(default_factory=list)
    approver: str = ""
    responsible_person: str = ""
    order_number: str = "dogovor"
    project_code: str = "/"
    legal_notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum(
            (li.line_total for li in self.line_items),
            Decimal("0"),
        ).quantize(Decimal("0.01"))

    @property
    def payment_reference(self) -> str:
        """eSLOG PQ reference: SI00{invoice number digits}."""
        digits = self.invoice_number.replace("-", "")
        return f"SI00{digits}"