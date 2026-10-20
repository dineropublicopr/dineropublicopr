"""Convierte el JSONL crudo en SQLite consultable y calcula las senales.

Decisiones que vale la pena conocer:

- Las fechas llegan como "/Date(1314849600000)/" en epoch milisegundos UTC. Puerto Rico es
  AST todo el ano (UTC-4, sin horario de verano), asi que convertimos a esa zona antes de
  quedarnos con la fecha. Si no, un contrato otorgado el 1 de julio aparece el 30 de junio
  y cae en el ano fiscal equivocado.

- Los nombres de contratista vienen sin normalizar: "JUAN PEREZ LLC", "Juan Perez, L.L.C."
  y "JUAN PÉREZ LLC" son el mismo. Generamos una clave normalizada para agrupar, pero
  guardamos el nombre original porque la clave es una heuristica, no una verdad.

- Las enmiendas aparecen como filas propias que comparten numero de contrato con el
  original. Eso permite medir cuanto se inflo un contrato sin pedir cada enmienda al API.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from catalogs import CONTRACTING_FORMS, FUNDS

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "contracts"
TAGS_DIR = ROOT / "data" / "raw" / "tags"
CATALOGS = ROOT / "data" / "raw" / "catalogs.json"
DB_PATH = ROOT / "data" / "out" / "contratos.sqlite"

AST = ZoneInfo("America/Puerto_Rico")
DATE_RE = re.compile(r"/Date\((-?\d+)\)/")

# Sufijos societarios que no distinguen una empresa de otra al agrupar.
CORP_SUFFIXES = re.compile(
    r"\b(LLC|L\s*L\s*C|INC|INCORPORATED|CORP|CORPORATION|PSC|P\s*S\s*C|LLP|LP|"
    r"CSP|C\s*S\s*P|SE|S\s*EN\s*C|DBA|AND|Y|THE|DE|DEL|LA|LAS|LOS|EL)\b"
)

SCHEMA = """
CREATE TABLE contracts (
    contract_id         INTEGER PRIMARY KEY,
    contract_number     TEXT    NOT NULL,
    amendment           TEXT,
    is_amendment        INTEGER NOT NULL,
    entity_id           INTEGER,
    entity_name         TEXT,
    date_of_grant       TEXT,
    fiscal_year         TEXT,
    grant_month         TEXT,
    effective_from      TEXT,
    effective_to        TEXT,
    service             TEXT,
    service_group       TEXT,
    amount_to_pay       REAL,
    amount_to_receive   REAL,
    cancellation_date   TEXT,
    has_amendments      INTEGER,
    document_id         TEXT,
    contracting_form_id INTEGER,
    contracting_form    TEXT,
    fund_id             INTEGER,
    fund                TEXT
);

CREATE TABLE contractors (
    contract_id     INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    name_key        TEXT    NOT NULL
);

CREATE TABLE entities (
    entity_code     INTEGER,
    entity_id       TEXT,
    name            TEXT,
    group_name      TEXT
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE INDEX idx_c_fy       ON contracts(fiscal_year);
CREATE INDEX idx_c_entity   ON contracts(entity_id);
CREATE INDEX idx_c_group    ON contracts(service_group);
CREATE INDEX idx_c_number   ON contracts(contract_number);
CREATE INDEX idx_c_amount   ON contracts(amount_to_pay);
CREATE INDEX idx_c_month    ON contracts(grant_month);
CREATE INDEX idx_c_form     ON contracts(contracting_form_id);
CREATE INDEX idx_c_fund     ON contracts(fund_id);
CREATE INDEX idx_ct_key     ON contractors(name_key);
CREATE INDEX idx_ct_cid     ON contractors(contract_id);
"""


def parse_aspnet_date(value) -> str | None:
    """"/Date(ms)/" -> "YYYY-MM-DD" en hora de Puerto Rico."""
    if not value:
        return None
    match = DATE_RE.search(str(value))
    if not match:
        return None
    moment = datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)
    return moment.astimezone(AST).date().isoformat()


def fiscal_year_of(iso_date: str | None) -> str | None:
    """El ano fiscal de PR corre del 1 de julio al 30 de junio."""
    if not iso_date:
        return None
    year, month = int(iso_date[:4]), int(iso_date[5:7])
    start = year if month >= 7 else year - 1
    return f"{start}-{str(start + 1)[2:]}"


def normalize_name(name: str) -> str:
    """Clave para agrupar el mismo contratista escrito de formas distintas.

    Es deliberadamente agresiva: quita acentos, puntuacion y sufijos societarios. Dos
    nombres con la misma clave son *probablemente* la misma entidad, no con certeza, asi
    que el nombre original siempre se conserva aparte.
    """
    text = unicodedata.normalize("NFD", name or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.upper()
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    text = CORP_SUFFIXES.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def iter_raw_rows():
    for path in sorted(RAW_DIR.glob("*/*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)


def load_tags() -> tuple[dict[int, int], dict[int, int]]:
    """Mapas contract_id -> metodo y contract_id -> fondo, generados por enrich.py."""
    forms: dict[int, int] = {}
    funds: dict[int, int] = {}
    for path in sorted(TAGS_DIR.glob("*/*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key, value in (payload.get("forms") or {}).items():
            forms[int(key)] = value
        for key, value in (payload.get("funds") or {}).items():
            funds[int(key)] = value
    return forms, funds


def build() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    form_tags, fund_tags = load_tags()
    print(f"etiquetas: metodo={len(form_tags):,}  fondos={len(fund_tags):,}")

    contracts: list[tuple] = []
    contractor_rows: list[tuple] = []
    seen: set[int] = set()

    for row in iter_raw_rows():
        contract_id = row.get("ContractId")
        if contract_id is None or contract_id in seen:
            continue
        seen.add(contract_id)

        grant = parse_aspnet_date(row.get("DateOfGrant"))
        amendment = row.get("Amendment")
        form_id = form_tags.get(contract_id)
        fund_id = fund_tags.get(contract_id)
        contracts.append(
            (
                contract_id,
                row.get("ContractNumber") or "",
                amendment,
                1 if amendment else 0,
                row.get("EntityId"),
                html.unescape(row.get("EntityName") or "") or None,
                grant,
                fiscal_year_of(grant),
                grant[:7] if grant else None,
                parse_aspnet_date(row.get("EffectiveDateFrom")),
                parse_aspnet_date(row.get("EffectiveDateTo")),
                html.unescape(row.get("Service") or "") or None,
                html.unescape(row.get("ServiceGroup") or "") or None,
                row.get("AmountToPay"),
                row.get("AmountToReceive"),
                parse_aspnet_date(row.get("CancellationDate")),
                1 if row.get("HasAmendments") else 0,
                row.get("DocumentWithoutSocialSecurityId"),
                form_id,
                CONTRACTING_FORMS.get(form_id) if form_id else None,
                fund_id,
                FUNDS.get(fund_id) if fund_id else None,
            )
        )

        for contractor in row.get("Contractors") or []:
            # El registro guarda los nombres con entidades HTML: "R&amp;R INVESTORS" en vez
            # de "R&R INVESTORS". Afecta a unos 7,300 nombres y, si no se corrige, parte en
            # dos la agrupacion del mismo contratista.
            name = html.unescape(contractor.get("Name") or "").strip()
            if name:
                contractor_rows.append((contract_id, name, normalize_name(name)))

    conn.executemany(
        f"INSERT INTO contracts VALUES ({','.join('?' * 22)})", contracts
    )
    conn.executemany("INSERT INTO contractors VALUES (?,?,?)", contractor_rows)

    if CATALOGS.exists():
        catalogs = json.loads(CATALOGS.read_text(encoding="utf-8"))
        conn.executemany(
            "INSERT INTO entities VALUES (?,?,?,?)",
            [
                (e.get("Code"), e.get("Id"), e.get("Name"), e.get("GroupName"))
                for e in catalogs.get("entities", [])
            ],
        )

    conn.executemany(
        "INSERT INTO meta VALUES (?,?)",
        [
            ("built_at", datetime.now(AST).isoformat(timespec="seconds")),
            ("contract_count", str(len(contracts))),
            ("source", "Registro de Contratos, Oficina de la Contralora de PR"),
        ],
    )

    conn.commit()
    print(f"contratos: {len(contracts):,}")
    print(f"contratistas (filas): {len(contractor_rows):,}")
    cur = conn.execute("SELECT COUNT(DISTINCT name_key) FROM contractors")
    print(f"contratistas unicos (normalizados): {cur.fetchone()[0]:,}")
    cur = conn.execute(
        "SELECT fiscal_year, COUNT(*), SUM(amount_to_pay) FROM contracts "
        "GROUP BY fiscal_year ORDER BY fiscal_year"
    )
    print("\npor ano fiscal:")
    for fy, count, total in cur.fetchall():
        print(f"  {fy or '(sin fecha)':<10} {count:>8,}  ${total or 0:>18,.2f}")
    conn.close()
    print(f"\nbase: {DB_PATH}")


if __name__ == "__main__":
    build()
