"""Descarga contratos del Registro de la Contralora a JSONL crudo, mes por mes.

Se corta por mes en vez de por ano porque: las paginas son de 1000 y un ano trae ~80,000
contratos, asi que un fallo a mitad de camino no debe costar la descarga completa. Cada mes
queda en su propio archivo y se puede reanudar.

Uso:
    python3 ingest.py                      # los anos fiscales por defecto
    python3 ingest.py 2023-24 2024-25      # anos especificos
    python3 ingest.py --refresh 2026-27    # vuelve a bajar aunque ya exista
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date
from pathlib import Path

from ocpr_client import OcprClient

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "contracts"
PAGE_SIZE = 1000

# Anos fiscales por defecto: los tres ultimos cerrados mas el corriente.
DEFAULT_FISCAL_YEARS = ["2023-24", "2024-25", "2025-26", "2026-27"]


def fiscal_year_months(fiscal_year: str) -> list[tuple[date, date]]:
    """Los 12 meses de un ano fiscal de PR (1 julio - 30 junio)."""
    start_year = int(fiscal_year.split("-")[0])
    months = []
    for offset in range(12):
        month = 7 + offset
        year = start_year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        last_day = calendar.monthrange(year, month)[1]
        months.append((date(year, month, 1), date(year, month, last_day)))
    return months


def fetch_month(client: OcprClient, first: date, last: date) -> list[dict]:
    """Todos los contratos otorgados en un mes, paginando hasta agotarlos."""
    date_from = first.strftime("%d/%m/%Y")
    date_to = last.strftime("%d/%m/%Y")

    head = client.search(
        start=0, length=1, DateOfGrantFrom=date_from, DateOfGrantTo=date_to
    )
    expected = head.get("recordsFiltered") or 0
    if expected == 0:
        return []

    rows: list[dict] = []
    start = 0
    while start < expected:
        page = client.search(
            start=start,
            length=PAGE_SIZE,
            DateOfGrantFrom=date_from,
            DateOfGrantTo=date_to,
        )
        batch = page.get("data") or []
        if not batch:
            break
        rows.extend(batch)
        start += len(batch)
        print(f"      {len(rows):>7,} / {expected:,}", end="\r", flush=True)

    print(f"      {len(rows):>7,} / {expected:,}        ")
    if len(rows) != expected:
        print(
            f"      aviso: se esperaban {expected:,} y llegaron {len(rows):,}",
            file=sys.stderr,
        )
    return rows


def ingest_fiscal_year(
    client: OcprClient, fiscal_year: str, refresh: bool = False
) -> int:
    out_dir = RAW_DIR / fiscal_year
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0

    for first, last in fiscal_year_months(fiscal_year):
        target = out_dir / f"{first:%Y-%m}.jsonl"
        if target.exists() and not refresh:
            existing = sum(1 for _ in target.open(encoding="utf-8"))
            print(f"   {first:%Y-%m}  ya existe ({existing:,})")
            total += existing
            continue

        # Un mes en el futuro no tiene contratos todavia.
        if first > date.today():
            continue

        print(f"   {first:%Y-%m}")
        rows = fetch_month(client, first, last)
        tmp = target.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp.replace(target)
        total += len(rows)

    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fiscal_years", nargs="*", default=None)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="vuelve a bajar meses que ya estan en disco",
    )
    args = parser.parse_args()

    years = args.fiscal_years or DEFAULT_FISCAL_YEARS
    client = OcprClient()
    client.open_session()

    grand_total = 0
    for fiscal_year in years:
        print(f"\n=== Ano fiscal {fiscal_year} ===")
        grand_total += ingest_fiscal_year(client, fiscal_year, args.refresh)

    print(f"\nTotal en disco: {grand_total:,} contratos")


if __name__ == "__main__":
    main()
