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

Tambien revisa lo que alimenta Cabilderos, Legal y Conexiones. Antes esos tres archivos se
publicaban sin mirarlos: si el registro de Justicia cambiaba de formato y la lista de firmas
salia vacia, el sitio la mostraba vacia.

- cabilderos.json tiene que traer firmas, cada una con su inscripcion; la lista no puede
  encogerse a menos de la mitad de una semana a otra, ni la del Senado (que solo crece)
  bajar mas de 10%;
- red.json no puede venir vacio, ni traer una conexion hacia un punto que no existe, ni
  perder mas de 30% de sus puntos;
- en legal.json cada senal tiene que nombrar la entidad, estar cruzada con un contratista
  y citar al menos una fuente.

Y da avisos que no detienen la corrida: una senal legal revisada a mano hace mas de 45 dias
(un caso puede haberse desestimado o resuelto), o una senal curada que dejo de cruzar con
contratos.

Uso:
    python3 pipeline/verificar.py
    python3 pipeline/verificar.py --anterior otro/resumen.json
    python3 pipeline/verificar.py --permitir-baja
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESUMEN = ROOT / "web" / "datos" / "resumen.json"
RESUMEN_GIT = "web/datos/resumen.json"

MIN_CONTRACTS = 50_000
MAX_TOTAL_DROP = 0.005
MAX_YEAR_DROP = 0.02
MAX_AMOUNT_DROP = 0.05

DATOS = ROOT / "web" / "datos"
DATOS_GIT = "web/datos"

# Las inscripciones de cabilderos vencen y se renuevan, asi que la lista de firmas se mueve
# mas que la de contratos. Perder mas de la mitad en una semana no es movimiento normal.
MAX_FIRMS_DROP = 0.5
# El registro del Senado es acumulativo desde 2017: solo crece.
MAX_SENADO_DROP = 0.1
MAX_NODES_DROP = 0.3
# Una acusacion puede desestimarse o terminar en sentencia. La fecha `verificado_el` la pone
# una persona cuando abre las fuentes; pasado este plazo se avisa para que vuelva a abrirlas.
MAX_DIAS_SIN_VERIFICAR = 45


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


def check_conexiones(
    cabilderos,
    red,
    legal,
    cab_anterior: dict | None = None,
    red_anterior: dict | None = None,
    permitir_baja: bool = False,
    hoy: date | None = None,
) -> tuple[list[str], list[str]]:
    """Revisa cabilderos.json, red.json y legal.json. Devuelve (problemas, avisos).

    Los problemas detienen la publicacion; los avisos solo se imprimen. `permitir_baja`
    apaga las comparaciones contra lo publicado, no los controles de consistencia interna.
    """
    problemas: list[str] = []
    avisos: list[str] = []
    hoy = hoy or date.today()

    # ---- Cabilderos ----
    firmas = cabilderos.get("firmas") if isinstance(cabilderos, dict) else None
    senado = (cabilderos.get("senado") or []) if isinstance(cabilderos, dict) else []
    if not isinstance(firmas, list) or not firmas:
        problemas.append(
            "cabilderos.json no trae ninguna firma (el registro de Justicia pudo cambiar de "
            "formato o no hay copia anterior)"
        )
        firmas = []
    else:
        declaradas = (cabilderos.get("resumen") or {}).get("firmas")
        if declaradas != len(firmas):
            problemas.append(
                f"cabilderos.json dice {declaradas!r} firmas en el resumen y trae {len(firmas):,}"
            )
        sin_inscripcion = sum(1 for f in firmas if not (isinstance(f, dict) and f.get("registros")))
        if sin_inscripcion:
            problemas.append(f"cabilderos.json trae {sin_inscripcion:,} firma(s) sin inscripcion")

    if isinstance(cab_anterior, dict) and not permitir_baja:
        antes = len(cab_anterior.get("firmas") or [])
        if antes and len(firmas) < antes * (1 - MAX_FIRMS_DROP):
            problemas.append(
                f"las firmas de cabilderos bajaron de {antes:,} a {len(firmas):,} "
                f"(se tolera {MAX_FIRMS_DROP:.0%})"
            )
        antes_senado = len(cab_anterior.get("senado") or [])
        if antes_senado and len(senado) < antes_senado * (1 - MAX_SENADO_DROP):
            problemas.append(
                f"las inscripciones del Senado bajaron de {antes_senado:,} a {len(senado):,} "
                f"(se tolera {MAX_SENADO_DROP:.0%})"
            )

    # ---- Grafo ----
    nodos = red.get("nodos") if isinstance(red, dict) else None
    aristas = red.get("aristas") if isinstance(red, dict) else None
    if not isinstance(nodos, list) or not isinstance(aristas, list) or not nodos or not aristas:
        problemas.append("red.json viene vacio: no hay puntos o no hay conexiones")
    else:
        ids = [n[0] for n in nodos]
        conocidos = set(ids)
        if len(conocidos) != len(ids):
            problemas.append("red.json trae puntos con el mismo identificador")
        rotas = sum(1 for a in aristas if a[0] not in conocidos or a[1] not in conocidos)
        if rotas:
            problemas.append(f"red.json trae {rotas:,} conexion(es) hacia un punto que no existe")
        firmas_en_red = sum(1 for n in nodos if n[1] == "f")
        if firmas and firmas_en_red != len(firmas):
            problemas.append(
                f"red.json dibuja {firmas_en_red:,} firmas y cabilderos.json trae {len(firmas):,}"
            )
        if isinstance(red_anterior, dict) and not permitir_baja:
            antes = len(red_anterior.get("nodos") or [])
            if antes and len(nodos) < antes * (1 - MAX_NODES_DROP):
                problemas.append(
                    f"los puntos del mapa bajaron de {antes:,} a {len(nodos):,} "
                    f"(se tolera {MAX_NODES_DROP:.0%})"
                )

    # ---- Legal ----
    senales = legal.get("senales") if isinstance(legal, dict) else None
    if not isinstance(senales, list):
        problemas.append("legal.json no trae la lista de senales")
    else:
        for senal in senales:
            nombre = senal.get("entidad") or "(sin nombre)"
            if not senal.get("entidad"):
                problemas.append("legal.json trae una senal sin entidad")
            if not (senal.get("contratista") or {}).get("clave"):
                problemas.append(f"legal: {nombre} no esta cruzada con ningun contratista")
            if not senal.get("fuentes"):
                problemas.append(f"legal: {nombre} no cita ninguna fuente")
            if senal.get("automatica"):
                continue
            verificado = str(senal.get("verificado_el") or "")[:10]
            try:
                dias = (hoy - date.fromisoformat(verificado)).days
            except ValueError:
                avisos.append(f"legal: {nombre} no dice cuando se verifico por ultima vez")
                continue
            if dias > MAX_DIAS_SIN_VERIFICAR:
                avisos.append(
                    f"legal: {nombre} se verifico por ultima vez el {verificado}, hace {dias} dias. "
                    "Abre las fuentes y confirma que el estatus del caso no cambio"
                )
        sin_contratos = legal.get("curadas_sin_contratos") or 0
        if sin_contratos:
            avisos.append(
                f"legal: {sin_contratos} senal(es) curada(s) no cruzan con ningun contratista "
                "y no se muestran"
            )

    return problemas, avisos


def _load_json(path: Path):
    """El JSON de un archivo, o None si no existe o no se puede leer."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_published(name: str):
    """La version de web/datos/<name> del ultimo commit, o None."""
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{DATOS_GIT}/{name}"], cwd=ROOT, capture_output=True, check=False
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout.decode("utf-8"))
    except (OSError, ValueError):
        return None


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

    # Cabilderos, Legal y Conexiones. Se leen de la misma carpeta que el resumen.
    datos = nuevo_path.parent
    conexiones = {name: _load_json(datos / f"{name}.json") for name in ("cabilderos", "red", "legal")}
    faltan = sorted(name for name, value in conexiones.items() if value is None)
    avisos: list[str] = []
    if faltan:
        problemas.append(
            "no se generaron o no son JSON valido: " + ", ".join(f"{name}.json" for name in faltan)
        )
    else:
        mas, avisos = check_conexiones(
            conexiones["cabilderos"],
            conexiones["red"],
            conexiones["legal"],
            cab_anterior=_load_published("cabilderos.json"),
            red_anterior=_load_published("red.json"),
            permitir_baja=args.permitir_baja,
        )
        problemas.extend(mas)

    # En GitHub Actions, ::warning:: lo deja a la vista en el resumen de la corrida.
    marca = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "aviso: "
    for linea in avisos:
        print(f"{marca}{linea}")

    if problemas:
        print("ERROR: los datos nuevos no pasan el control de cordura. No se publica.", file=sys.stderr)
        for linea in problemas:
            print(f"  - {linea}", file=sys.stderr)
        sys.exit(1)

    antes = f"{anterior['contratos']:,}" if anterior and isinstance(anterior.get("contratos"), int) else "sin dato"
    print(f"ok: {nuevo['contratos']:,} contratos (antes: {antes})")
    print(
        f"ok: {len(conexiones['cabilderos']['firmas']):,} firmas de cabilderos, "
        f"{len(conexiones['red']['nodos']):,} puntos en el mapa, "
        f"{len(conexiones['legal']['senales']):,} senales legales"
    )


if __name__ == "__main__":
    main()
