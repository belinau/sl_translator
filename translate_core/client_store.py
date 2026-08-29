"""Secure SQLite client store with Fernet encryption (GDPR-grade).

ALL personally-identifiable fields are encrypted at rest: client name,
address, postal code, city, account holder, VAT ID, IBAN, BIC, bank,
registration number, and the responsible-persons JSON blob.  Client ids
are opaque 8-hex UUIDs — deriving them from names would leak the name
even with encrypted rows.  Invoice records (line descriptions carry
personal names) are encrypted as well.

Key derivation: PBKDF2-SHA256 over ``config.STORAGE_SECRET`` with a fixed
salt, cached per process.  Decryption happens only in memory, never logged.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import config
from translate_core.invoice_models import ClientRecord

log = logging.getLogger(__name__)

_SALT = b"sl_translator_client_store_v2"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    address     TEXT NOT NULL DEFAULT '',
    postal_code TEXT NOT NULL DEFAULT '',
    city        TEXT NOT NULL DEFAULT '',
    country     TEXT NOT NULL DEFAULT 'Slovenija',
    country_code TEXT NOT NULL DEFAULT 'SI',
    vat_id      TEXT NOT NULL DEFAULT '',
    vat_obliged INTEGER NOT NULL DEFAULT 0,
    iban        TEXT NOT NULL DEFAULT '',
    iban_compact TEXT NOT NULL DEFAULT '',
    bic         TEXT NOT NULL DEFAULT '',
    bank_name   TEXT NOT NULL DEFAULT '',
    maticna     TEXT NOT NULL DEFAULT '',
    account_holder TEXT NOT NULL DEFAULT '',
    default_order_number TEXT NOT NULL DEFAULT 'dogovor',
    default_project_code TEXT NOT NULL DEFAULT '/',
    default_doc_type TEXT NOT NULL DEFAULT 'Pogodba',
    default_doc_ref TEXT NOT NULL DEFAULT '',
    use_eracun  INTEGER NOT NULL DEFAULT 0,
    rates       TEXT NOT NULL DEFAULT '{}',
    responsible_persons TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoice_counter (
    year         INTEGER PRIMARY KEY,
    last_number  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_number   TEXT PRIMARY KEY,
    year             INTEGER NOT NULL,
    invoice_type     TEXT NOT NULL,
    client_id        TEXT NOT NULL,
    issue_date       TEXT NOT NULL,
    due_date         TEXT NOT NULL,
    service_date_from TEXT,
    service_date_to  TEXT,
    responsible_person TEXT,
    approver         TEXT,
    order_number     TEXT,
    project_code     TEXT,
    total            TEXT NOT NULL,
    line_items       TEXT NOT NULL,
    pdf_path         TEXT,
    xml_path         TEXT,
    xlsx_path        TEXT,
    created_at       TEXT NOT NULL
);
"""

# Fields encrypted at rest — every column containing personal data.
_ENCRYPTED_FIELDS = (
    "name", "address", "postal_code", "city", "vat_id",
    "iban", "iban_compact", "bic", "bank_name",
    "maticna", "account_holder",
)


def _new_client_id() -> str:
    """Opaque 8-hex client id — never derived from the name."""
    return uuid.uuid4().hex[:8]


class ClientStore:
    """Encrypted SQLite client + invoice-number store."""

    # One key derivation per process — PBKDF2 at 480k iterations is ~0.15 s
    # and the storage secret is process-static.
    _fernet_cache: dict[str, Fernet] = {}

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or config.CLIENT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        secret = config.STORAGE_SECRET or "fallback"
        if secret not in ClientStore._fernet_cache:
            ClientStore._fernet_cache[secret] = self._make_fernet()
        self._fernet = ClientStore._fernet_cache[secret]
        self._init_schema()

    # ── key derivation ──────────────────────────────────────────────

    def _make_fernet(self) -> Fernet:
        secret = (config.STORAGE_SECRET or "fallback").encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=_SALT,
            iterations=480_000,
        )
        raw = kdf.derive(secret)
        key = base64.urlsafe_b64encode(raw)
        return Fernet(key)

    # ── schema ──────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript(_SCHEMA)
            # Migrate: add columns missing in older DBs.
            ccols = {r[1] for r in conn.execute("PRAGMA table_info(clients)").fetchall()}
            for col, ddl in [
                ("vat_obliged", "INTEGER NOT NULL DEFAULT 0"),
                ("account_holder", "TEXT NOT NULL DEFAULT ''"),
                ("default_order_number", "TEXT NOT NULL DEFAULT 'dogovor'"),
                ("default_project_code", "TEXT NOT NULL DEFAULT '/'"),
                ("default_doc_type", "TEXT NOT NULL DEFAULT 'Pogodba'"),
                ("default_doc_ref", "TEXT NOT NULL DEFAULT ''"),
            ]:
                if col not in ccols:
                    conn.execute(f"ALTER TABLE clients ADD COLUMN {col} {ddl}")
            icols = {r[1] for r in conn.execute("PRAGMA table_info(invoices)").fetchall()}
            for col in ("approver", "pdf_path", "xml_path", "xlsx_path"):
                if col not in icols:
                    conn.execute(f"ALTER TABLE invoices ADD COLUMN {col} TEXT")
            os.chmod(self._db_path, 0o600)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── encryption helpers ──────────────────────────────────────────

    def _encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def _decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            log.warning("Decryption failed for field — returning empty")
            return ""

    def _enc_json(self, obj: Any) -> str:
        return self._encrypt(json.dumps(obj, ensure_ascii=False))

    def _dec_json(self, ciphertext: str, default: Any) -> Any:
        plaintext = self._decrypt(ciphertext)
        if not plaintext:
            return default
        try:
            return json.loads(plaintext)
        except json.JSONDecodeError:
            return default

    # ── client CRUD ─────────────────────────────────────────────────

    def add_client(self, data: dict[str, Any]) -> str:
        """Insert a new client.  Returns the opaque client id."""
        cid = _new_client_id()
        now = datetime.now().isoformat(timespec="seconds")
        iban = data.get("iban", "")
        iban_compact = iban.replace(" ", "") if iban else ""
        rates_json = json.dumps(
            {k: str(v) for k, v in (data.get("rates") or {}).items()},
            ensure_ascii=False,
        )

        plain: dict[str, Any] = {
            "id": cid,
            "country": data.get("country", "Slovenija"),
            "country_code": data.get("country_code", "SI"),
            "default_order_number": data.get("default_order_number", "dogovor"),
            "default_project_code": data.get("default_project_code", "/"),
            "default_doc_type": data.get("default_doc_type", "Pogodba"),
            "default_doc_ref": data.get("default_doc_ref", ""),
            "vat_obliged": 1 if data.get("vat_obliged") else 0,
            "use_eracun": 1 if data.get("use_eracun") else 0,
            "rates": rates_json,
            "created_at": now,
            "updated_at": now,
        }
        for f in _ENCRYPTED_FIELDS:
            if f == "iban_compact":
                plain[f] = self._encrypt(iban_compact)
            else:
                plain[f] = self._encrypt(str(data.get(f, "")))
        plain["responsible_persons"] = self._enc_json(data.get("responsible_persons") or [])

        conn = self._connect()
        try:
            conn.execute(
                f"INSERT INTO clients ({', '.join(plain.keys())}) "
                f"VALUES ({', '.join('?' * len(plain))})",
                tuple(plain.values()),
            )
            conn.commit()
        finally:
            conn.close()
        return cid

    def get_client(self, cid: str) -> ClientRecord | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM clients WHERE id = ?", (cid,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_clients(self) -> list[ClientRecord]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM clients ORDER BY name").fetchall()
        finally:
            conn.close()
        return [self._row_to_record(r) for r in rows]

    def list_clients_brief(self) -> list[dict[str, Any]]:
        """List clients for UI dropdowns (decrypted in memory only)."""
        clients = self.list_clients()
        return [
            {"id": c.id, "name": c.name, "use_eracun": c.use_eracun}
            for c in clients
        ]

    def find_client_by_name(self, name: str) -> ClientRecord | None:
        """Look up a client by exact (decrypted) name."""
        for c in self.list_clients():
            if c.name == name:
                return c
        return None

    def update_client(self, cid: str, data: dict[str, Any]) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        iban = data.get("iban", "")
        iban_compact = iban.replace(" ", "") if iban else ""
        rates_json = json.dumps(
            {k: str(v) for k, v in (data.get("rates") or {}).items()},
            ensure_ascii=False,
        )

        plain = {
            "country": data.get("country", "Slovenija"),
            "country_code": data.get("country_code", "SI"),
            "default_order_number": data.get("default_order_number", "dogovor"),
            "default_project_code": data.get("default_project_code", "/"),
            "default_doc_type": data.get("default_doc_type", "Pogodba"),
            "default_doc_ref": data.get("default_doc_ref", ""),
            "vat_obliged": 1 if data.get("vat_obliged") else 0,
            "use_eracun": 1 if data.get("use_eracun") else 0,
            "rates": rates_json,
            "updated_at": now,
        }
        enc: dict[str, str] = {}
        for f in _ENCRYPTED_FIELDS:
            if f == "iban_compact":
                enc[f] = self._encrypt(iban_compact)
            else:
                enc[f] = self._encrypt(str(data.get(f, "")))
        enc["responsible_persons"] = self._enc_json(data.get("responsible_persons") or [])

        all_fields = {**plain, **enc}
        set_clause = ", ".join(f"{k} = ?" for k in all_fields)
        values = tuple(all_fields.values()) + (cid,)

        conn = self._connect()
        try:
            cur = conn.execute(f"UPDATE clients SET {set_clause} WHERE id = ?", values)
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_client(self, cid: str) -> bool:
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM clients WHERE id = ?", (cid,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ── invoice counter ────────────────────────────────────────────

    def next_invoice_number(self, year: int) -> str:
        """Atomically allocate the next invoice number for *year*."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT last_number FROM invoice_counter WHERE year = ?", (year,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO invoice_counter (year, last_number) VALUES (?, 1)",
                    (year,),
                )
                num = 1
            else:
                num = row["last_number"] + 1
                conn.execute(
                    "UPDATE invoice_counter SET last_number = ? WHERE year = ?",
                    (num, year),
                )
            conn.commit()
        finally:
            conn.close()
        return f"{year}-{num:03d}"

    # ── invoice record ─────────────────────────────────────────────

    def record_invoice(self, data: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        enc_items = self._enc_json(data.get("line_items") or [])
        enc_person = self._encrypt(data.get("responsible_person", "") or "")
        enc_approver = self._encrypt(data.get("approver", "") or "")
        conn = self._connect()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO invoices
                   (invoice_number, year, invoice_type, client_id,
                    issue_date, due_date, service_date_from, service_date_to,
                    responsible_person, approver, order_number, project_code,
                    total, line_items, pdf_path, xml_path, xlsx_path, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data["invoice_number"],
                    int(data["issue_date"][:4]),
                    data["invoice_type"],
                    data["client_id"],
                    str(data["issue_date"]),
                    str(data["due_date"]),
                    str(data.get("service_date_from", "")),
                    str(data.get("service_date_to", "")),
                    enc_person,
                    enc_approver,
                    data.get("order_number", ""),
                    data.get("project_code", ""),
                    str(data["total"]),
                    enc_items,
                    data.get("pdf_path") or None,
                    data.get("xml_path") or None,
                    data.get("xlsx_path") or None,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def list_invoices(self, year: int | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if year:
                rows = conn.execute(
                    "SELECT * FROM invoices WHERE year = ? ORDER BY invoice_number DESC",
                    (year,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM invoices ORDER BY invoice_number DESC"
                ).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["line_items"] = self._dec_json(d.get("line_items") or "", [])
            d["responsible_person"] = self._decrypt(d.get("responsible_person") or "")
            d["approver"] = self._decrypt(d.get("approver") or "")
            out.append(d)
        return out

    # ── helpers ────────────────────────────────────────────────────

    def _row_to_record(self, row: sqlite3.Row) -> ClientRecord:
        rates_raw = json.loads(row["rates"] or "{}")
        rates: dict[str, Decimal] = {}
        for k, v in rates_raw.items():
            try:
                rates[k] = Decimal(str(v))
            except (InvalidOperation, ValueError):
                log.warning("Bad rate %s=%r — skipped", k, v)
        try:
            persons = json.loads(self._decrypt(row["responsible_persons"] or "") or "[]")
        except json.JSONDecodeError:
            persons = []

        def _enc(col: str) -> str:
            return self._decrypt(row[col])

        return ClientRecord(
            id=row["id"],
            name=_enc("name"),
            address=_enc("address"),
            postal_code=_enc("postal_code"),
            city=_enc("city"),
            country=row["country"],
            country_code=row["country_code"],
            vat_id=_enc("vat_id"),
            vat_obliged=bool(row["vat_obliged"]),
            iban=_enc("iban"),
            iban_compact=_enc("iban_compact"),
            bic=_enc("bic"),
            bank_name=_enc("bank_name"),
            maticna=_enc("maticna"),
            account_holder=_enc("account_holder"),
            default_order_number=row["default_order_number"],
            default_project_code=row["default_project_code"],
            default_doc_type=row["default_doc_type"],
            default_doc_ref=row["default_doc_ref"],
            use_eracun=bool(row["use_eracun"]),
            rates=rates,
            responsible_persons=persons,
        )

    @staticmethod
    def mask_iban(iban: str) -> str:
        if not iban or len(iban) < 8:
            return iban or ""
        parts = iban.replace(" ", "")
        return f"{parts[:4]} **** **** **** {parts[-4:]}"

    @staticmethod
    def mask_name(name: str) -> str:
        """Show first word + first letter of the rest (GDPR-safe preview)."""
        if not name:
            return ""
        words = name.split()
        if len(words) == 1:
            return f"{words[0][:2]}***"
        return f"{words[0][0]}*** {words[-1][0]}***"

    @staticmethod
    def mask_vat(vat: str) -> str:
        if not vat or len(vat) < 4:
            return vat or ""
        return f"{vat[:2]}***{vat[-2:]}"