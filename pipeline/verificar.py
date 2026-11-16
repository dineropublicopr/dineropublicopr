"""Control de cordura antes de publicar: compara los datos nuevos con los ya publicados.

Corre despues del pipeline y antes de hacer commit, desplegar o cargar D1. Si algo no
cuadra, termina con codigo 1 y la corrida se detiene: el sitio sigue mostrando los ultimos
datos buenos, que es mejor que publicar un total incorrecto con apariencia de dato oficial.

Antes el unico control era "al menos 50,000 contratos". Con mas de 270,000 publicados, eso
dejaba pasar la perdida de un ano fiscal entero. Ahora el punto de comparacion es el
resumen.json del commit anterior (lo que el sitio muestra hoy):

- el total de contratos no puede bajar mas de 0.5%;
- ningun ano fiscal ya publicado puede desaparecer ni bajar mas de 2%;
- el monto total no puede bajar mas de 5%;
- el total tiene que ser la suma de los anos fiscales;
- sigue habiendo un minimo absoluto, por si no existe resumen anterior.

Los contratos casi nunca desaparecen del Registro, asi que una baja mayor casi siempre es
una descarga incompleta. Si alguna vez la baja es real, se comprueba en el portal y se
corre con --permitir-baja (en el workflow: la casilla "permitir_baja" al lanzarlo a mano).

Uso:
    python3 pipeline/verificar.py
    python3 pipeline/verificar.py --anterior otro/resumen.json
    python3 pipeline/verificar.py --permitir-baja
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESUMEN = ROOT / "web" / "datos" / "resumen.json"
RESUMEN_GIT = "web/datos/resumen.json"

MIN_CONTRACTS = 50_000
MAX_TOTAL_DROP = 0.005
MAX_YEAR_DROP = 0.02
MAX_AMOUNT_DROP = 0.05


def _years(resumen: dict) -> dict[str, int]:
    return {
        str(row.get("ano")): int(row.get("contratos") or 0)
        for row in (resumen.get("anos_fiscales") or [])
        if row.get("ano")
    }


def check(nuevo: dict, anterior: dict | None, permitir_baja: bool = False) -> list[str]:
    """Devuelve la lista de problemas. Lista vacia significa que se puede publicar.

    `permitir_baja` apaga solo las comparaciones contra el resumen anterior. Los controles
    de consistencia interna y el minimo absoluto no se pueden saltar.
    """
    problemas: list[str] = []

    total = nuevo.get("contratos")
    if not isinstance(total, int) or isinstance(total, bool):
        return [f"resumen.json no trae un total de contratos valido: {total!r}"]
    if total < MIN_CONTRACTS:
        problemas.append(f"muy pocos contratos: {total:,} (minimo {MIN_CONTRACTS:,})")

    anos = _years(nuevo)
    if not anos:
        problemas.append("resumen.json no trae ningun ano fiscal")
    else:
        if any(n <= 0 for n in anos.values()):
            vacios = sorted(a for a, n in anos.items() if n <= 0)
            problemas.append(f"anos fiscales sin contratos: {', '.join(vacios)}")
        # Un contrato sin ano fiscal no entra en la suma, asi que se admite una diferencia
        # minima; una diferencia grande indica que el resumen se genero a medias.
        suma = sum(anos.values())
        if abs(suma - total) > max(10, total * 0.001):
            problemas.append(
                f"el total ({total:,}) no cuadra con la suma de los anos fiscales ({suma:,})"
            )

    if anterior is None or permitir_baja:
        return problemas

    previo = anterior.get("contratos")
    if isinstance(previo, int) and previo > 0:
        minimo = previo * (1 - MAX_TOTAL_DROP)
        if total < minimo:
            problemas.append(
                f"el total bajo de {previo:,} a {total:,} contratos "
                f"({(previo - total) / previo:.1%}; se tolera {MAX_TOTAL_DROP:.1%})"
            )

    for ano, antes in sorted(_years(anterior).items()):
        if antes <= 0:
            continue
        ahora = anos.get(ano)
        if ahora is None:
            problemas.append(f"el ano fiscal {ano} desaparecio (tenia {antes:,} contratos)")
        elif ahora < antes * (1 - MAX_YEAR_DROP):
            problemas.append(
                f"el ano fiscal {ano} bajo de {antes:,} a {ahora:,} contratos "
                f"({(antes - ahora) / antes:.1%}; se tolera {MAX_YEAR_DROP:.0%})"
            )

    monto_antes = anterior.get("monto_total")
    monto_ahora = nuevo.get("monto_total")
    if (
        isinstance(monto_antes, (int, float))
        and isinstance(monto_ahora, (int, float))
        and monto_antes > 0
        and monto_ahora < monto_antes * (1 - MAX_AMOUNT_DROP)
    ):
        problemas.append(
            f"el monto total bajo de ${monto_antes:,.0f} a ${monto_ahora:,.0f} "
            f"({(monto_antes - monto_ahora) / monto_antes:.1%}; se tolera {MAX_AMOUNT_DROP:.0%})"
        )

    return problemas


def load_previous(path: str | None = None) -> dict | None:
    """El resumen publicado: un archivo dado, o el del ultimo commit del repositorio."""
    try:
        if path:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        result = subprocess.run(
            ["git", "show", f"HEAD:{RESUMEN_GIT}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout.decode("utf-8"))
    except (OSError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--nuevo", default=str(RESUMEN), help="resumen.json recien generado")
    parser.add_argument("--anterior", default=None, help="resumen.json publicado (por defecto, el de HEAD)")
    parser.add_argument(
        "--permitir-baja",
        action="store_true",
        help="acepta una baja frente al resumen anterior (compruebala antes en el portal)",
    )
    args = parser.parse_args()

    nuevo_path = Path(args.nuevo)
    if not nuevo_path.exists():
        sys.exit("ERROR: no se genero resumen.json")
    try:
        nuevo = json.loads(nuevo_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        sys.exit(f"ERROR: resumen.json no es JSON valido: {exc}")

    anterior = load_previous(args.anterior)
    if anterior is None:
        print("aviso: no hay resumen anterior con que comparar; solo se aplica el minimo absoluto")

    problemas = check(nuevo, anterior, permitir_baja=args.permitir_baja)
    if problemas:
        print("ERROR: los datos nuevos no pasan el control de cordura. No se publica.", file=sys.stderr)
        for linea in problemas:
            print(f"  - {linea}", file=sys.stderr)
        sys.exit(1)

    antes = f"{anterior['contratos']:,}" if anterior and isinstance(anterior.get("contratos"), int) else "sin dato"
    print(f"ok: {nuevo['contratos']:,} contratos (antes: {antes})")


if __name__ == "__main__":
    main()
