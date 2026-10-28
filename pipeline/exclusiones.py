"""Baja las listas federales de exclusion y se queda solo con ENTIDADES de Puerto Rico.

Una exclusion federal es una determinacion oficial de que una entidad no puede recibir
fondos de un programa federal, casi siempre por fraude, sobornos o una condena relacionada.
Es lo mas cercano a la "inhabilidad para contratar" del Articulo 3.4 de la Ley 2-2018 que se
puede consultar en bloque y sin pedir permiso.

Fuentes:

- LEIE, de la Oficina del Inspector General de HHS (oig.hhs.gov). Exclusiones de Medicare,
  Medicaid y demas programas federales de salud. CSV publico, sin llave, actualizado cada
  mes. Incluye el codigo 1128(b)(8): entidades excluidas porque su dueno o quien las
  controla fue sancionado. Es exactamente el vinculo dueno-empresa que interesa, y lo
  establece la fuente, no este proyecto.

- SAM.gov, exclusiones de toda la contratacion federal. Requiere una llave gratuita de
  api.sam.gov en la variable de entorno SAM_API_KEY. Sin ella, este paso se salta. La copia
  "publica" de SAM que aparece en catalog.data.gov esta congelada en 2020 y no se usa:
  mostraria exclusiones que ya vencieron.

Por que SOLO entidades de Puerto Rico: al probar el cruce por nombre contra todo el pais
salieron coincidencias como "H", "M" o "THE BASE" (sanciones de OFAC por alias) y empresas de
Maryland o Wisconsin con nombre generico. Un cruce por nombre sin la misma jurisdiccion
produce mas falsos positivos que hallazgos, y aqui un falso positivo le pone una bandera
roja a una empresa inocente. Las personas naturales se descartan siempre.

Salida: data/raw/exclusiones.json. El CSV completo de LEIE (~10 MB) no se versiona.
"""

from __future__ import annotations

import csv
import io
import json
import os
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ocpr_client import UA, _ssl_context

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "raw" / "exclusiones.json"

LEIE_URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"
SAM_URL = "https://api.sam.gov/data-services/v1/extracts"

AST = ZoneInfo("America/Puerto_Rico")

# Codigos de LEIE que vale la pena explicar al lector, segun 42 U.S.C. 1320a-7.
LEIE_TYPES = {
    "1128a1": "Condena por delito relacionado con un programa federal de salud",
    "1128a2": "Condena por negligencia o abuso de pacientes",
    "1128a3": "Condena grave por fraude en el cuidado de salud",
    "1128a4": "Condena grave relacionada con sustancias controladas",
    "1128b1": "Condena menor por fraude en el cuidado de salud",
    "1128b2": "Condena menor por obstruccion de una investigacion",
    "1128b4": "Licencia revocada o suspendida",
    "1128b5": "Suspendida o excluida de otro programa federal o estatal",
    "1128b7": "Fraude, sobornos u otras actividades prohibidas",
    "1128b8": "Entidad controlada por una persona sancionada",
    "1128b14": "Incumplimiento de un prestamo estudiantil de salud",
}


def download(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_context()))
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def ymd(value: str) -> str | None:
    """'20250619' -> '2025-06-19'. LEIE usa 0 o vacio para 'no aplica'."""
    value = (value or "").strip()
    if len(value) != 8 or not value.isdigit() or value == "00000000":
        return None
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def fetch_leie() -> list[dict]:
    text = download(LEIE_URL).decode("utf-8-sig", "replace")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        # Una fila es entidad cuando trae BUSNAME y no trae apellido.
        if row.get("STATE") != "PR" or not row.get("BUSNAME") or row.get("LASTNAME"):
            continue
        code = (row.get("EXCLTYPE") or "").strip()
        rows.append(
            {
                "fuente": "LEIE",
                "nombre": row["BUSNAME"].strip(),
                "ciudad": (row.get("CITY") or "").strip(),
                "tipo_entidad": (row.get("GENERAL") or "").strip(),
                "codigo": code,
                "motivo": LEIE_TYPES.get(code, "Exclusion federal de programas de salud"),
                "desde": ymd(row.get("EXCLDATE")),
                "hasta": None,
                "agencia": "HHS-OIG",
            }
        )
    return rows


def fetch_sam(api_key: str) -> list[dict]:
    """Extracto publico V2 de exclusiones de SAM.gov (open.gsa.gov/api/sam-entity-extracts-api).

    Con fileType=EXCLUSION y sin fecha, el API entrega el extracto diario mas reciente: un ZIP
    con un CSV de columnas Classification, Name, State / Province, Excluding Agency,
    Exclusion Type, Active Date y Termination Date, que se leen por nombre y no por posicion.
    """
    url = f"{SAM_URL}?api_key={api_key}&fileType=EXCLUSION"
    payload = download(url, timeout=600)
    if payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
            text = archive.read(name).decode("utf-8-sig", "replace")
    else:
        text = payload.decode("utf-8-sig", "replace")
    return parse_sam_csv(text)


def parse_sam_csv(text: str) -> list[dict]:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("Classification") != "Firm":
            continue
        if (row.get("State / Province") or "").strip() != "PR":
            continue
        if (row.get("Excluding Agency") or "").startswith("TREAS-OFAC"):
            continue
        rows.append(
            {
                "fuente": "SAM",
                "nombre": (row.get("Name") or "").strip(),
                "ciudad": (row.get("City") or "").strip(),
                "tipo_entidad": "Firm",
                "codigo": (row.get("CT Code") or "").strip(),
                "motivo": (row.get("Exclusion Type") or "").strip(),
                "desde": (row.get("Active Date") or "").strip() or None,
                "hasta": (row.get("Termination Date") or "").strip() or None,
                "agencia": (row.get("Excluding Agency") or "").strip(),
            }
        )
    return rows


def main() -> None:
    previous = {}
    if OUT_PATH.exists():
        previous = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    out = dict(previous)
    now = datetime.now(AST).isoformat(timespec="seconds")

    sources = [("leie", fetch_leie, "LEIE (HHS-OIG)")]
    sam_key = os.environ.get("SAM_API_KEY", "").strip()
    if sam_key:
        sources.append(("sam", lambda: fetch_sam(sam_key), "SAM.gov"))
    else:
        print("SAM.gov: sin SAM_API_KEY, se salta")

    for key, fetcher, label in sources:
        try:
            records = fetcher()
            out[key] = {"descargado": now, "entidades_pr": records}
            print(f"{label}: {len(records):,} entidades de Puerto Rico")
        except Exception as err:  # noqa: BLE001
            kept = len((previous.get(key) or {}).get("entidades_pr") or [])
            print(f"{label}: FALLO ({err}). Se conserva la copia anterior ({kept}).")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"guardado: {OUT_PATH}")


if __name__ == "__main__":
    main()
