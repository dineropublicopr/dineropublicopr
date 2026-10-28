"""Baja los registros publicos de cabilderos y los guarda tal como vienen.

Hay dos registros distintos y ninguno cubre lo que cubre el otro:

- Departamento de Justicia (registrodecabilderos.pr.gov), creado por la OE-2019-031. Cubre
  el cabildeo ante las agencias ejecutivas, que es justo donde se otorgan los contratos.
  Es el unico que publica CLIENTES y PERSONAL AUTORIZADO. La busqueda publica esta detras
  de una pagina de condiciones de uso: se acepta con un POST que lleva el token
  anti-forgery, igual que el portal de la Contralora. Las condiciones prohiben la venta de
  la informacion con fines de lucro; este proyecto no vende nada y la republica gratis,
  que es lo que el propio registro dice perseguir: "que la informacion sea de facil acceso
  al publico en general".

  Ojo: solo muestra las inscripciones vigentes (unas 40 firmas en octubre de 2026). En abril
  de 2026 el Senado le pidio a Justicia la lista completa desde 2019 porque el registro en
  linea esta incompleto. Lo que no aparece aqui no significa que no exista.

- Senado (senado.pr.gov/registro-cabilderos), por la Orden Administrativa 05-16. Cubre el
  cabildeo legislativo desde 2017. Publica nombre, tipo y fecha, con una certificacion en
  PDF que NO trae clientes. Sirve para ver la historia y quien trabaja para que firma.

La Camara de Representantes tiene su propio registro, pero camara.pr.gov no respondia desde
fuera de Puerto Rico al escribir esto, asi que no se incluye.

El resultado va a data/raw/cabilderos.json. Si una fuente falla, se conserva la copia
anterior de esa fuente en vez de borrarla: el sitio sigue mostrando los ultimos datos buenos.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from zoneinfo import ZoneInfo

from ocpr_client import UA, _ssl_context

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "raw" / "cabilderos.json"

JUSTICIA = "https://registrodecabilderos.pr.gov/"
SENADO = "https://senado.pr.gov/"

AST = ZoneInfo("America/Puerto_Rico")
DELAY = 1.5
MAX_SENADO_PAGES = 60  # tope de seguridad; en 2026 son 11 paginas de 30


class TableParser(HTMLParser):
    """Extrae las filas de las tablas como listas de celdas, con el primer enlace de cada una.

    Mas robusto que un regex contra el HTML: no le importan los saltos de linea, el orden de
    los atributos ni las comillas simples o dobles.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self._row: list[dict] | None = None
        self._cell: dict | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {"text": "", "href": None, "header": tag == "th"}
        elif tag == "a" and self._cell is not None and self._cell["href"] is None:
            self._cell["href"] = attrs.get("href")
        elif tag == "br" and self._cell is not None:
            self._cell["text"] += "\n"

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data


def table_rows(page: str) -> list[list[dict]]:
    parser = TableParser()
    parser.feed(page)
    return [row for row in parser.rows if not all(cell["header"] for cell in row)]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def split_list(text: str) -> list[str]:
    """Las celdas de clientes y personal vienen separadas por punto y coma."""
    seen: set[str] = set()
    out: list[str] = []
    for part in re.split(r"[;\n]", text or ""):
        item = clean(part).strip(" ,.")
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


class Session:
    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self._last = 0.0
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPSHandler(context=_ssl_context()),
        )

    def fetch(self, url: str, data: dict | None = None) -> str:
        """Una peticion, nunca mas de una cada DELAY segundos."""
        wait = DELAY - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es-PR,es;q=0.9",
                "Referer": url,
            },
            method="POST" if body is not None else "GET",
        )
        with self._opener.open(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8-sig", "replace")


def fetch_justicia() -> list[dict]:
    """Inscripciones vigentes del registro del Departamento de Justicia."""
    session = Session()
    landing = session.fetch(urllib.parse.urljoin(JUSTICIA, "Lobbyist"))
    match = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', landing)
    if not match:
        raise RuntimeError("Justicia: no aparece el token de la pagina de condiciones")

    # Equivale a pulsar ACEPTO en las condiciones de uso.
    page = session.fetch(
        urllib.parse.urljoin(JUSTICIA, "Lobbyist/Details"),
        {"__RequestVerificationToken": match.group(1)},
    )

    records = []
    for cells in table_rows(page):
        if len(cells) < 4:
            continue
        name = clean(cells[0]["text"])
        if not name:
            continue
        href = cells[0]["href"]
        records.append(
            {
                "nombre": name,
                "registro": clean(cells[1]["text"]),
                "clientes": split_list(cells[2]["text"]),
                "personal": split_list(cells[3]["text"]),
                "certificado": urllib.parse.urljoin(JUSTICIA, href) if href else None,
            }
        )
    if not records:
        raise RuntimeError("Justicia: la tabla vino vacia; puede haber cambiado la pagina")
    return records


MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}


def parse_fecha(text: str) -> str | None:
    """'Martes, 03 de febrero de 2026' -> '2026-02-03'."""
    match = re.search(r"(\d{1,2}) de (\w+) de (\d{4})", text or "")
    if not match:
        return None
    month = MONTHS.get(match.group(2).lower())
    if not month:
        return None
    return f"{match.group(3)}-{month:02d}-{int(match.group(1)):02d}"


def fetch_senado() -> list[dict]:
    """Registro de cabilderos del Senado, todas las paginas.

    La pagina carga la tabla por AJAX desde /api/loadTabla/cabilderos/{n}, de 30 en 30.
    """
    session = Session()
    records = []
    for page_no in range(MAX_SENADO_PAGES):
        page = session.fetch(urllib.parse.urljoin(SENADO, f"api/loadTabla/cabilderos/{page_no}?"))
        rows = [cells for cells in table_rows(page) if len(cells) >= 3]
        if not rows:
            break
        for cells in rows:
            href = cells[0]["href"]
            records.append(
                {
                    "nombre": clean(cells[0]["text"]),
                    "tipo": clean(cells[1]["text"]),
                    "fecha": parse_fecha(cells[2]["text"]),
                    # Los nombres de archivo traen espacios y acentos sin codificar.
                    "pdf": urllib.parse.quote(href.replace("http://", "https://", 1), safe=":/%")
                    if href else None,
                }
            )
    if not records:
        raise RuntimeError("Senado: no se encontraron filas; puede haber cambiado la pagina")
    return records


def main() -> None:
    previous = {}
    if OUT_PATH.exists():
        previous = json.loads(OUT_PATH.read_text(encoding="utf-8"))

    out = dict(previous)
    now = datetime.now(AST).isoformat(timespec="seconds")

    for key, fetcher, label in (
        ("justicia", fetch_justicia, "Departamento de Justicia"),
        ("senado", fetch_senado, "Senado"),
    ):
        try:
            records = fetcher()
            out[key] = {"descargado": now, "registros": records}
            print(f"{label}: {len(records):,} inscripciones")
        except Exception as err:  # noqa: BLE001 - cualquier fallo de red o de formato
            # Degradacion suave: se queda la copia anterior, si la hay.
            kept = len((previous.get(key) or {}).get("registros") or [])
            print(f"{label}: FALLO ({err}). Se conserva la copia anterior ({kept} filas).")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"guardado: {OUT_PATH}")


if __name__ == "__main__":
    main()
