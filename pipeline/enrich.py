"""Etiqueta cada contrato con su metodo de contratacion y su origen de fondos.

Por que hace falta: el endpoint contract/search acepta ContractingFormId y FundId como
*filtros*, pero no devuelve esos campos en cada fila. Asi que la unica forma de saber como
se adjudico un contrato es preguntar "dame los contratos de este mes con este metodo" y
quedarse con los IDs. Son unas 400 peticiones adicionales, que a ~1.5s cada una es un rato,
pero sin esto no hay forma de filtrar por RFP ni RFQ.

Resultado: data/raw/tags/<ano fiscal>/<mes>.json con
    {"forms": {"<contract_id>": <form_id>}, "funds": {"<contract_id>": <fund_id>}}
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from catalogs import CONTRACTING_FORMS, FUNDS
from ingest import DEFAULT_FISCAL_YEARS, PAGE_SIZE, fiscal_year_months
from ocpr_client import OcprClient

TAGS_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "tags"


def collect_ids(
    client: OcprClient, date_from: str, date_to: str, **filters
) -> list[int]:
    """IDs de contrato que cumplen un filtro, paginando hasta agotarlos."""
    head = client.search(
        start=0, length=1, DateOfGrantFrom=date_from, DateOfGrantTo=date_to, **filters
    )
    expected = head.get("recordsFiltered") or 0
    if expected == 0:
        return []

    ids: list[int] = []
    start = 0
    while start < expected:
        page = client.search(
            start=start,
            length=PAGE_SIZE,
            DateOfGrantFrom=date_from,
            DateOfGrantTo=date_to,
            **filters,
        )
        batch = page.get("data") or []
        if not batch:
            break
        ids.extend(row["ContractId"] for row in batch if row.get("ContractId"))
        start += len(batch)
    return ids


def tag_month(client: OcprClient, first: date, last: date) -> dict:
    date_from = first.strftime("%d/%m/%Y")
    date_to = last.strftime("%d/%m/%Y")

    forms: dict[str, int] = {}
    for form_id in CONTRACTING_FORMS:
        for contract_id in collect_ids(
            client, date_from, date_to, ContractingFormId=form_id
        ):
            forms[str(contract_id)] = form_id

    funds: dict[str, int] = {}
    for fund_id in FUNDS:
        for contract_id in collect_ids(client, date_from, date_to, FundId=fund_id):
            funds[str(contract_id)] = fund_id

    return {"forms": forms, "funds": funds}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fiscal_years", nargs="*", default=None)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    client = OcprClient()
    client.open_session()

    for fiscal_year in args.fiscal_years or DEFAULT_FISCAL_YEARS:
        out_dir = TAGS_DIR / fiscal_year
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n=== {fiscal_year} ===")

        for first, last in fiscal_year_months(fiscal_year):
            target = out_dir / f"{first:%Y-%m}.json"
            if target.exists() and not args.refresh:
                print(f"   {first:%Y-%m}  ya existe")
                continue
            if first > date.today():
                continue

            tags = tag_month(client, first, last)
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(tags, ensure_ascii=False), encoding="utf-8")
            tmp.replace(target)
            print(
                f"   {first:%Y-%m}  metodo={len(tags['forms']):>6,}  "
                f"fondos={len(tags['funds']):>6,}"
            )


if __name__ == "__main__":
    main()
