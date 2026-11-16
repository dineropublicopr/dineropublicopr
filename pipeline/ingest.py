"""Descarga contratos del Registro de la Contralora a JSONL crudo, mes por mes.

Se corta por mes en vez de por ano porque: las paginas son de 1000 y un ano trae ~80,000
contratos, asi que un fallo a mitad de camino no debe costar la descarga completa. Cada mes
queda en su propio archivo y se puede reanudar.

Uso:
    python3 ingest.py                      # los anos fiscales por defecto
    python3 ingest.py 2023-24 2024-25      # anos especificos
    python3 ingest.py --refresh 2026-27    # vuelve a bajar aunque ya exista
    python3 ingest.py --refresh-years 2025-26 2026-27
                                           # todos los anos por defecto en una sola pasada:
                                           # baja lo que falta y refresca solo esos dos

Proteccion contra descargas malas: un mes ya guardado nunca se reemplaza por un resultado
incompleto, vacio o mucho mas pequeno. En ese caso se conserva el archivo anterior, se
sigue con los demas meses y el script termina con codigo 1 para que la corrida falle a la
vista en vez de publicar menos contratos de los que hay. Si la baja es real (la Contralora
elimino registros), se acepta a conciencia con --aceptar-bajas.
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date
from pathlib import Path

from ocpr_client import OcprClient, PortalError

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "contracts"
PAGE_SIZE = 1000

# Anos fiscales por defecto: los tres ultimos cerrados mas el corriente.
DEFAULT_FISCAL_YEARS = ["2023-24", "2024-25", "2025-26", "2026-27"]

# Cuanto puede encogerse un mes ya guardado sin que se considere sospechoso. Los contratos
# casi nunca desaparecen del Registro: lo normal es que un mes crezca con registros
# retroactivos. Se tolera una baja pequena (correcciones de la agencia) y nada mas.
MAX_DROP_FRACTION = 0.02
MAX_DROP_ROWS = 5


class IncompleteDownload(Exception):
    """El portal anuncio mas registros de los que entrego."""


def suspicious_drop(existing: int, new: int) -> str | None:
    """Dice por que no se debe reemplazar un archivo de `existing` filas por uno de `new`.

    Devuelve None cuando el reemplazo es razonable. La regla sirve igual para contratos
    (ingest.py) que para etiquetas (enrich.py).
    """
    if existing <= 0 or new >= existing:
        return None
    if new == 0:
        return f"el resultado nuevo viene vacio y el archivo guardado tiene {existing:,}"
    allowed = max(MAX_DROP_ROWS, int(existing * MAX_DROP_FRACTION))
    if existing - new > allowed:
        return (
            f"el resultado nuevo trae {new:,} y el archivo guardado tiene {existing:,} "
            f"(baja de {existing - new:,}; se toleran {allowed:,})"
        )
    return None


def count_lines(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


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
    """Todos los contratos otorgados en un mes, paginando hasta agotarlos.

    Lanza IncompleteDownload si llegan menos filas de las que el portal anuncio. Antes solo
    imprimia un aviso y devolvia lo que hubiera, y ese resultado parcial terminaba
    escribiendose encima del mes bueno.
    """
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
    if len(rows) < expected:
        raise IncompleteDownload(
            f"el portal anuncio {expected:,} contratos y entrego {len(rows):,}"
        )
    return rows


def ingest_fiscal_year(
    client: OcprClient,
    fiscal_year: str,
    refresh: bool = False,
    problems: list[str] | None = None,
    accept_drops: bool = False,
    today: date | None = None,
) -> int:
    """Baja los meses de un ano fiscal. Devuelve cuantos contratos quedan en disco.

    Los meses que no se pudieron bajar bien se anotan en `problems` y conservan su archivo
    anterior; quien llama decide que hacer con esa lista (main termina con codigo 1).
    """
    out_dir = RAW_DIR / fiscal_year
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    today = today or date.today()
    if problems is None:
        problems = []

    for first, last in fiscal_year_months(fiscal_year):
        target = out_dir / f"{first:%Y-%m}.jsonl"
        existing = count_lines(target) if target.exists() else None
        if existing is not None and not refresh:
            print(f"   {first:%Y-%m}  ya existe ({existing:,})")
            total += existing
            continue

        # Un mes en el futuro no tiene contratos todavia.
        if first > today:
            continue

        print(f"   {first:%Y-%m}")
        label = f"{fiscal_year} {first:%Y-%m}"
        try:
            rows = fetch_month(client, first, last)
        except (IncompleteDownload, PortalError) as exc:
            kept = "se conserva el archivo anterior" if existing is not None else "no hay archivo anterior"
            problems.append(f"{label}: {exc}; {kept}")
            print(f"      ERROR: {exc}; {kept}", file=sys.stderr, flush=True)
            total += existing or 0
            continue

        reason = None if accept_drops else suspicious_drop(existing or 0, len(rows))
        if reason:
            problems.append(f"{label}: {reason}; se conserva el archivo anterior")
            print(
                f"      ERROR: {reason}; se conserva el archivo anterior",
                file=sys.stderr,
                flush=True,
            )
            total += existing or 0
            continue

        tmp = target.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp.replace(target)
        total += len(rows)

    return total


def report_problems(problems: list[str], what: str) -> None:
    """Imprime el resumen de fallos y termina con codigo 1 si hubo alguno."""
    if not problems:
        return
    print(
        f"\nERROR: {len(problems)} mes(es) de {what} no se pudieron actualizar bien. "
        "Los archivos anteriores se conservaron y NO se debe publicar esta corrida:",
        file=sys.stderr,
    )
    for line in problems:
        print(f"  - {line}", file=sys.stderr)
    print(
        "Si la baja es real y esta verificada contra el portal, repite con --aceptar-bajas.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("fiscal_years", nargs="*", default=None)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="vuelve a bajar meses que ya estan en disco",
    )
    parser.add_argument(
        "--refresh-years",
        nargs="+",
        default=[],
        metavar="ANO",
        help="vuelve a bajar solo estos anos fiscales; de los demas, solo lo que falta",
    )
    parser.add_argument(
        "--aceptar-bajas",
        action="store_true",
        help="permite reemplazar un mes por un resultado mas pequeno (verificalo antes)",
    )
    args = parser.parse_args()

    years = list(args.fiscal_years or DEFAULT_FISCAL_YEARS)
    # Un ano pedido para refrescar se procesa aunque no este en la lista.
    years += [year for year in args.refresh_years if year not in years]
    # La sesion se abre sola en la primera peticion, con los mismos reintentos que el resto.
    client = OcprClient()

    grand_total = 0
    problems: list[str] = []
    for fiscal_year in years:
        print(f"\n=== Ano fiscal {fiscal_year} ===")
        grand_total += ingest_fiscal_year(
            client,
            fiscal_year,
            refresh=args.refresh or fiscal_year in args.refresh_years,
            problems=problems,
            accept_drops=args.aceptar_bajas,
        )

    print(f"\nTotal en disco: {grand_total:,} contratos")
    report_problems(problems, "contratos")


if __name__ == "__main__":
    main()
