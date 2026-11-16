"""Corre el pipeline completo, en orden. Esto es lo que ejecuta la automatizacion semanal.

    python3 run.py              # actualizacion normal: solo baja lo que falta
    python3 run.py --full       # vuelve a bajar todo desde cero

El paso de ingesta es incremental: los meses ya guardados no se vuelven a pedir, salvo los
de los dos anos fiscales mas recientes, que se refrescan siempre porque las agencias siguen
registrando contratos con fecha retroactiva.

    python3 run.py --aceptar-bajas   # solo despues de comprobar en el portal que la baja
                                     # de contratos que detuvo la corrida anterior es real
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent


def step(name: str, args: list[str]) -> None:
    print(f"\n{'=' * 58}\n  {name}\n{'=' * 58}", flush=True)
    result = subprocess.run([sys.executable, *args], cwd=HERE)
    if result.returncode != 0:
        raise SystemExit(f"fallo en: {name} (codigo {result.returncode})")


def recent_fiscal_years() -> list[str]:
    """El ano fiscal corriente y el anterior: los que todavia reciben registros nuevos."""
    today = date.today()
    start = today.year if today.month >= 7 else today.year - 1
    return [
        f"{start - 1}-{str(start)[2:]}",
        f"{start}-{str(start + 1)[2:]}",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--full", action="store_true", help="rehace todo desde cero")
    parser.add_argument(
        "--aceptar-bajas",
        action="store_true",
        help="deja que un mes se reemplace por un resultado mas pequeno",
    )
    args = parser.parse_args()
    extra = ["--aceptar-bajas"] if args.aceptar_bajas else []

    step("Catalogos de agencias y servicios", ["catalogs.py"])

    if args.full:
        step("Ingesta completa", ["ingest.py", "--refresh", *extra])
        step("Etiquetado completo", ["enrich.py", "--refresh", *extra])
    else:
        # Una sola pasada por paso: baja los meses que falten de cualquier ano y refresca
        # los dos anos recientes, donde siguen entrando contratos retroactivos. Antes eran
        # dos invocaciones seguidas ("faltantes" y luego "refresco"), y cuando la cache de
        # datos crudos se perdia, los dos anos recientes se bajaban dos veces.
        recent = recent_fiscal_years()
        step("Ingesta: meses faltantes y anos recientes", ["ingest.py", "--refresh-years", *recent, *extra])
        step("Etiquetado: meses faltantes y anos recientes", ["enrich.py", "--refresh-years", *recent, *extra])

    step("Construccion de la base SQLite", ["build_db.py"])
    # Las enmiendas se piden despues de construir la base, porque es de ahi que se saca
    # cuales contratos las tienen. Solo los de $1M o mas, por tiempo.
    step("Enmiendas de contratos grandes", ["amendments.py"])
    step("Generacion del API estatico", ["export_api.py"])
    # Fuentes externas al Registro de Contratos. Si una falla, su script conserva la copia
    # anterior y termina bien: un registro caido no debe tumbar la actualizacion semanal.
    step("Registros de cabilderos", ["cabilderos.py"])
    step("Listas federales de exclusion", ["exclusiones.py"])
    step("Cruce de cabilderos y senales legales", ["conexiones.py"])

    print("\nPipeline completo.")


if __name__ == "__main__":
    main()
