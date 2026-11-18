"""Historial de lo que el registro de cabilderos de Justicia ha publicado.

El registro en línea del Departamento de Justicia solo muestra las inscripciones vigentes.
Cuando una vence, desaparece de la página y con ella la lista de clientes que declaraba:
no queda forma de saber que existió. Este módulo guarda, semana a semana, lo que el sitio
alcanzó a ver, en web/datos/cabilderos_historial.json.

Lo que el historial dice y lo que no:

- «primera_vez» y «ultima_vez» son las fechas de la primera y la última DESCARGA en que
  apareció una inscripción o un cliente. No son la fecha en que alguien se inscribió ni la
  fecha en que dejó de cabildear: antes de «desde» este sitio no miraba.
- «vigente» dice si estaba en la descarga más reciente.
- Solo entra lo que cabilderos.json ya publica. Un cliente que podría ser una persona
  natural no se guarda, y si el filtro se vuelve más estricto, sale también del historial.

Vive en web/datos y no en data/raw porque esa carpeta es lo único que la automatización
guarda en el repositorio de una semana a otra; la cache de datos crudos se puede perder.

    python3 pipeline/historial.py --desde-git

reconstruye el historial con las fotos semanales de cabilderos.json que quedaron en el
repositorio. Se corre una vez, al estrenar el historial; después lo mantiene conexiones.py
en cada corrida. Repetirlo no daña nada: las fechas solo se ensanchan.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from build_db import normalize_name

ROOT = Path(__file__).resolve().parent.parent
NOMBRE = "cabilderos_historial.json"
PUBLICADO = "web/datos/cabilderos.json"

AST = ZoneInfo("America/Puerto_Rico")

NOTA = (
    "Justicia solo publica las inscripciones vigentes. Este archivo guarda lo que este sitio "
    "vio en ese registro en cada descarga, a partir de la fecha «desde». «primera_vez» y "
    "«ultima_vez» son las fechas de la primera y la última descarga en que apareció cada "
    "inscripción o cliente; no son la fecha en que se inscribió ni la fecha en que dejó de "
    "cabildear. «vigente» dice si estaba en la descarga más reciente («hasta»). Los clientes "
    "que podrían ser personas naturales no se incluyen."
)


def _clave(texto: str | None) -> str:
    return normalize_name(texto or "") or (texto or "").upper().strip()


def actualizar_historial(previo: dict | None, foto: dict, es_persona=None) -> dict | None:
    """Funde una foto de cabilderos.json con el historial acumulado.

    Devuelve el historial nuevo, o `previo` sin tocar si la foto no sirve: sin firmas o sin
    fecha de descarga no hay nada que afirmar, y dar por vencidas todas las inscripciones
    porque una descarga falló sería inventar un dato.

    El orden de las fotos no importa: las fechas solo se ensanchan, y los datos
    descriptivos (nombre de la firma, certificado) se toman de la foto más reciente.
    """
    firmas = foto.get("firmas") or []
    fecha = (((foto.get("fuentes") or {}).get("justicia") or {}).get("descargado") or "")[:10]
    if not firmas or not fecha:
        return previo

    previo = previo or {}
    inscripciones = {
        (_clave(i["firma"]), i["numero"]): dict(i) for i in previo.get("inscripciones") or []
    }
    clientes = {
        (_clave(c["firma"]), _clave(c["cliente"])): dict(c) for c in previo.get("clientes") or []
    }

    def visto(entry: dict) -> bool:
        """Anota la fecha de esta foto. Devuelve si es la más reciente que se le conoce."""
        reciente = fecha >= (entry.get("ultima_vez") or "")
        entry["primera_vez"] = min(entry.get("primera_vez") or fecha, fecha)
        entry["ultima_vez"] = max(entry.get("ultima_vez") or fecha, fecha)
        return reciente

    for firma in firmas:
        nombre = firma.get("nombre") or ""
        fk = _clave(nombre)
        for reg in firma.get("registros") or []:
            numero = reg.get("numero")
            if not numero:
                continue
            entry = inscripciones.setdefault((fk, numero), {"firma": nombre, "numero": numero})
            if visto(entry):
                entry["firma"] = nombre
                entry["trimestre"] = reg.get("trimestre") or entry.get("trimestre") or ""
                entry["certificado"] = reg.get("certificado") or entry.get("certificado")
        for cliente in firma.get("clientes") or []:
            declarado = cliente.get("nombre") or ""
            if not declarado:
                continue
            entry = clientes.setdefault(
                (fk, _clave(declarado)), {"firma": nombre, "cliente": declarado}
            )
            if visto(entry):
                entry["firma"] = nombre
                entry["cliente"] = declarado
                entry["con_contratos"] = bool(cliente.get("contratista"))
            entry["registros"] = sorted(
                set(entry.get("registros") or []) | set(cliente.get("registros") or []),
                reverse=True,
            )

    # La regla de «solo empresas» vale también para lo guardado. Un cliente cruzado con un
    # contratista se publica por ese cruce; el resto tiene que pasar el filtro de hoy.
    if es_persona is not None:
        clientes = {
            key: c for key, c in clientes.items()
            if c.get("con_contratos") or not es_persona(c["cliente"])
        }

    hasta = max(fecha, previo.get("hasta") or "")
    for entry in (*inscripciones.values(), *clientes.values()):
        entry["vigente"] = entry["ultima_vez"] == hasta

    lista_i = [
        {
            "firma": i["firma"],
            "numero": i["numero"],
            "trimestre": i.get("trimestre") or "",
            "certificado": i.get("certificado"),
            "primera_vez": i["primera_vez"],
            "ultima_vez": i["ultima_vez"],
            "vigente": i["vigente"],
        }
        for i in sorted(
            inscripciones.values(), key=lambda i: (i["vigente"], _clave(i["firma"]), i["numero"])
        )
    ]
    lista_c = [
        {
            "firma": c["firma"],
            "cliente": c["cliente"],
            "con_contratos": bool(c.get("con_contratos")),
            "registros": c.get("registros") or [],
            "primera_vez": c["primera_vez"],
            "ultima_vez": c["ultima_vez"],
            "vigente": c["vigente"],
        }
        for c in sorted(
            clientes.values(), key=lambda c: (c["vigente"], _clave(c["firma"]), _clave(c["cliente"]))
        )
    ]
    fechas = [e["primera_vez"] for e in (*lista_i, *lista_c)]
    return {
        "generado": datetime.now(AST).isoformat(timespec="seconds"),
        "fuente": {
            "nombre": "Registro de Cabilderos, Departamento de Justicia (OE-2019-031)",
            "url": "https://registrodecabilderos.pr.gov/Lobbyist",
        },
        "nota": NOTA,
        "desde": min(fechas) if fechas else fecha,
        "hasta": hasta,
        "resumen": {
            "inscripciones": len(lista_i),
            "inscripciones_no_vigentes": sum(1 for i in lista_i if not i["vigente"]),
            "clientes": len(lista_c),
            "clientes_no_vigentes": sum(1 for c in lista_c if not c["vigente"]),
        },
        "inscripciones": lista_i,
        "clientes": lista_c,
    }


def fotos_de_git():
    """Las versiones de cabilderos.json que guarda el repositorio, de la más vieja a la nueva."""
    log = subprocess.run(
        ["git", "log", "--reverse", "--format=%H", "--", PUBLICADO],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    for commit in log.stdout.split():
        shown = subprocess.run(
            ["git", "show", f"{commit}:{PUBLICADO}"], cwd=ROOT, capture_output=True, check=False
        )
        if shown.returncode != 0:
            continue
        try:
            yield json.loads(shown.stdout.decode("utf-8"))
        except ValueError:
            continue


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--desde-git",
        action="store_true",
        required=True,
        help="reconstruye el historial con las fotos que guarda el repositorio",
    )
    parser.parse_args()

    # Import tardío: conexiones.py importa este módulo.
    from conexiones import may_be_person

    destino = ROOT / "web" / "datos" / NOMBRE
    historial = json.loads(destino.read_text(encoding="utf-8")) if destino.exists() else None
    fotos = 0
    for foto in fotos_de_git():
        historial = actualizar_historial(historial, foto, es_persona=may_be_person)
        fotos += 1
    if historial is None:
        raise SystemExit("no hay ninguna foto de cabilderos.json en el repositorio")

    destino.write_text(
        json.dumps(historial, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    r = historial["resumen"]
    print(
        f"{fotos} fotos, del {historial['desde']} al {historial['hasta']}: "
        f"{r['inscripciones']} inscripciones ({r['inscripciones_no_vigentes']} ya no vigentes), "
        f"{r['clientes']} clientes ({r['clientes_no_vigentes']} ya no vigentes)"
    )
    print(f"guardado: {destino}")


if __name__ == "__main__":
    main()
