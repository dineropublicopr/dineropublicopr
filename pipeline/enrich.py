"""Etiqueta cada contrato con su metodo de contratacion y su origen de fondos.

Por que hace falta: el endpoint contract/search acepta ContractingFormId y FundId como
*filtros*, pero no devuelve esos campos en cada fila. Asi que la unica forma de saber como
se adjudico un contrato es preguntar "dame los contratos de este mes con este metodo" y
quedarse con los IDs. Son unas 400 peticiones adicionales, que a ~1.5s cada una es un rato,
pero sin esto no hay forma de filtrar por RFP ni RFQ.

Resultado: data/raw/tags/<ano fiscal>/<mes>.json con
    {"forms": {"<contract_id>": <form_id>}, "funds": {"<contract_id>": <fund_id>}}

Igual que ingest.py, un mes ya etiquetado no se reemplaza por un resultado incompleto o
mucho mas pequeno: se conserva el anterior y el script termina con codigo 1. Perder las
etiquetas de un mes haria aparecer miles de contratos como "sin metodo registrado", que es
justo la senal principal del sitio.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from catalogs import CONTRACTING_FORMS, FUNDS
from ingest import (
    DEFAULT_FISCAL_YEARS,
    PAGE_SIZE,
    IncompleteDownload,
    fiscal_year_months,
    report_problems,
    suspicious_drop,
)
from ocpr_client import OcprClient, PortalError

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
    if start < expected:
        raise IncompleteDownload(
            f"el portal anuncio {expected:,} contratos para {filters} y entrego {start:,}"
        )
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


def existing_counts(target: Path) -> tuple[int, int] | None:
    """(metodos, fondos) del archivo ya guardado, o None si no existe o no se puede leer."""
    if not target.exists():
        return None
    try:
        saved = json.loads(target.read_text(encoding="utf-8"))
        return len(saved.get("forms") or {}), len(saved.get("funds") or {})
    except (OSError, ValueError, AttributeError):
        # Un archivo ilegible no protege nada: se deja reemplazar.
        return None


def enrich_fiscal_year(
    client: OcprClient,
    fiscal_year: str,
    refresh: bool = False,
    problems: list[str] | None = None,
    accept_drops: bool = False,
    today: date | None = None,
) -> None:
    out_dir = TAGS_DIR / fiscal_year
    out_dir.mkdir(parents=True, exist_ok=True)
    today = today or date.today()
    if problems is None:
        problems = []

    for first, last in fiscal_year_months(fiscal_year):
        target = out_dir / f"{first:%Y-%m}.json"
        if target.exists() and not refresh:
            print(f"   {first:%Y-%m}  ya existe")
            continue
        if first > today:
            continue

        label = f"{fiscal_year} {first:%Y-%m}"
        before = existing_counts(target)
        kept = "se conserva el archivo anterior" if target.exists() else "no hay archivo anterior"
        try:
            tags = tag_month(client, first, last)
        except (IncompleteDownload, PortalError) as exc:
            problems.append(f"{label}: {exc}; {kept}")
            print(f"   {first:%Y-%m}  ERROR: {exc}; {kept}", file=sys.stderr, flush=True)
            continue

        reason = None
        if before and not accept_drops:
            reason = suspicious_drop(before[0], len(tags["forms"])) or suspicious_drop(
                before[1], len(tags["funds"])
            )
        if reason:
            problems.append(f"{label}: {reason}; {kept}")
            print(f"   {first:%Y-%m}  ERROR: {reason}; {kept}", file=sys.stderr, flush=True)
            continue

        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(tags, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        print(
            f"   {first:%Y-%m}  metodo={len(tags['forms']):>6,}  "
            f"fondos={len(tags['funds']):>6,}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("fiscal_years", nargs="*", default=None)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--refresh-years",
        nargs="+",
        default=[],
        metavar="ANO",
        help="vuelve a etiquetar solo estos anos fiscales; de los demas, solo lo que falta",
    )
    parser.add_argument(
        "--aceptar-bajas",
        action="store_true",
        help="permite reemplazar un mes por un resultado mas pequeno (verificalo antes)",
    )
    args = parser.parse_args()

    years = list(args.fiscal_years or DEFAULT_FISCAL_YEARS)
    years += [year for year in args.refresh_years if year not in years]
    client = OcprClient()

    problems: list[str] = []
    for fiscal_year in years:
        print(f"\n=== {fiscal_year} ===")
        enrich_fiscal_year(
            client,
            fiscal_year,
            refresh=args.refresh or fiscal_year in args.refresh_years,
            problems=problems,
            accept_drops=args.aceptar_bajas,
        )

    report_problems(problems, "etiquetas")


if __name__ == "__main__":
    main()
