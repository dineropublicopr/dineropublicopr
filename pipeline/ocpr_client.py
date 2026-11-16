"""Cliente del API de consulta del Registro de Contratos (Oficina de la Contralora de PR).

El portal consultacontratos.ocpr.gov.pr es una app ASP.NET con DataTables server-side.
Expone JSON sobre POST, protegido por un token anti-forgery que hay que extraer del HTML
y reenviar junto con las cookies de sesion.
"""

from __future__ import annotations

import gzip
import http.client
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

BASE = "https://consultacontratos.ocpr.gov.pr/"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Columnas tal como las declara DataTables en /scripts/contract/search.
# El indice importa: `order` referencia columnas por posicion.
COLUMNS = [
    {"data": None, "name": "", "orderable": False, "searchable": False},
    {"data": "ContractNumber", "name": "", "orderable": True, "searchable": True},
    {"data": "Contractors", "name": "", "orderable": True, "searchable": True},
    {"data": "DateOfGrant", "name": "", "orderable": True, "searchable": True},
    {"data": "EffectiveDateFrom", "name": "", "orderable": True, "searchable": True},
    {"data": "EffectiveDateTo", "name": "", "orderable": True, "searchable": True},
    {"data": "AmountToPay", "name": "", "orderable": True, "searchable": True},
    {"data": "Service", "name": "", "orderable": True, "searchable": True},
    {"data": "EntityId", "name": "", "orderable": True, "searchable": True},
    {"data": "CancellationDate", "name": "", "orderable": True, "searchable": True},
]

# Reintentos. Una corrida hace entre 500 y 3,000 peticiones a un portal publico que a veces
# tarda, devuelve un 502 o deja caducar la sesion; sin reintentos, un solo tropiezo tumbaba
# la corrida completa (paso el 2026-10-21 a los 50 minutos). Se espera cada vez mas entre
# intentos para no martillar un servidor que ya esta en apuros.
BACKOFF_SECONDS = (5, 15, 45)
# Codigos HTTP que vale la pena repetir: saturacion o fallo temporal del servidor.
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
# Con estos el portal esta diciendo que la sesion o el token ya no valen.
SESSION_STATUS = frozenset({401, 403})


class PortalError(RuntimeError):
    """El portal no contesto algo utilizable despues de agotar los reintentos."""


def _ssl_context() -> ssl.SSLContext:
    """Contexto TLS con verificacion activa.

    El Python de python.org en macOS no trae CA bundle propio, asi que caemos al del
    sistema o a certifi antes que desactivar la verificacion.
    """
    for candidate in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
        if os.path.exists(candidate):
            return ssl.create_default_context(cafile=candidate)
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


FILTER_KEYS = (
    "EntityId",
    "ContractNumber",
    "ContractorName",
    "DateOfGrantFrom",
    "DateOfGrantTo",
    "EffectiveDateFrom",
    "EffectiveDateTo",
    "AmountFrom",
    "AmountTo",
    "ServiceGroupId",
    "ServiceId",
    "FundId",
    "ContractingFormId",
    "PCONumber",
)


class OcprClient:
    """Sesion contra el portal. Mantiene cookies y token anti-forgery."""

    def __init__(
        self,
        delay: float = 1.5,
        timeout: int = 60,
        backoff: tuple = BACKOFF_SECONDS,
    ):
        self.delay = delay
        self.timeout = timeout
        self.backoff = tuple(backoff)
        self.token: str | None = None
        self._last_request = 0.0
        # Se guarda como atributo para que las pruebas no tengan que esperar de verdad.
        self._sleep = time.sleep
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPSHandler(context=_ssl_context()),
        )

    def _throttle(self) -> None:
        """Nunca mas de una peticion cada `delay` segundos: es un servidor publico."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            self._sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def _read(self, resp) -> bytes:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw

    def open_session(self) -> str:
        """Carga la pagina de busqueda y extrae el token anti-forgery."""
        self._throttle()
        req = urllib.request.Request(
            BASE,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es-PR,es;q=0.9",
            },
        )
        with self._opener.open(req, timeout=self.timeout) as resp:
            html = self._read(resp).decode("utf-8-sig", "replace")

        match = re.search(
            r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', html
        )
        if not match:
            raise PortalError(
                "No se encontro __RequestVerificationToken: el portal cambio de estructura."
            )
        self.token = match.group(1)
        return self.token

    def _fetch_json(self, build_request) -> dict:
        """Hace la peticion y devuelve el JSON, reintentando los fallos pasajeros.

        `build_request` es una funcion sin argumentos que arma el Request. Se llama en cada
        intento, y no una sola vez, porque el token va en las cabeceras y puede haberse
        renovado entre un intento y el siguiente.

        Se reintenta ante: error de red o timeout, HTTP 5xx/408/429, respuesta que no es
        JSON (el portal contesta con la pagina HTML de inicio cuando la sesion caduca) y
        HTTP 401/403. En los dos ultimos casos se abre una sesion nueva antes de repetir.
        Cualquier otro 4xx es un error de la peticion misma y se propaga de inmediato:
        repetirlo daria lo mismo.
        """
        attempts = len(self.backoff) + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            renew_session = False
            try:
                if self.token is None:
                    self.open_session()
                self._throttle()
                with self._opener.open(build_request(), timeout=self.timeout) as resp:
                    # utf-8-sig, no utf-8: el servidor devuelve BOM de forma intermitente
                    # y json.loads lo rechaza con "Unexpected UTF-8 BOM". Rompio la corrida
                    # del 2026-10-21 despues de 50 minutos. Sin BOM es igual que utf-8.
                    return json.loads(self._read(resp).decode("utf-8-sig", "replace"))
            except urllib.error.HTTPError as err:
                if err.code in SESSION_STATUS:
                    renew_session = True
                elif err.code not in RETRY_STATUS:
                    raise
                last_error = err
            except json.JSONDecodeError as err:
                renew_session = True
                last_error = err
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.HTTPException,
                PortalError,
            ) as err:
                last_error = err

            if attempt == attempts - 1:
                break
            wait = self.backoff[attempt]
            print(
                f"      aviso: fallo del portal ({type(last_error).__name__}: {last_error}); "
                f"reintento {attempt + 1} de {attempts - 1} en {wait}s",
                file=sys.stderr,
                flush=True,
            )
            self._sleep(wait)
            if renew_session:
                # Se descarta el token; el proximo intento abre sesion de nuevo. Si esa
                # apertura falla, cuenta como un intento mas y no como un error aparte.
                self.token = None

        raise PortalError(
            f"el portal de la Contralora no respondio bien tras {attempts} intentos: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    def _post_json(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")

        def build() -> urllib.request.Request:
            return urllib.request.Request(
                urllib.parse.urljoin(BASE, path),
                data=body,
                headers={
                    "User-Agent": UA,
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Accept-Language": "es-PR,es;q=0.9",
                    "X-Requested-With": "XMLHttpRequest",
                    "__RequestVerificationToken": self.token,
                    "Referer": BASE,
                    "Origin": BASE.rstrip("/"),
                },
                method="POST",
            )

        return self._fetch_json(build)

    def search(
        self,
        start: int = 0,
        length: int = 100,
        order_col: int = 1,
        order_dir: str = "desc",
        **filters,
    ) -> dict:
        """Una pagina de resultados.

        Filtros utiles (todos opcionales):
          EntityId           agencia
          ContractorName     nombre del contratista
          DateOfGrantFrom/To fecha de otorgacion, formato DD/MM/YYYY
          AmountFrom/To      cuantia
          ServiceGroupId     categoria de servicio
          ServiceId          tipo de servicio
          FundId             origen de fondos (estatal / federal / ...)
          ContractingFormId  metodo de contratacion (subasta / RFP / RFQ / emergencia)
        """
        unknown = set(filters) - set(FILTER_KEYS)
        if unknown:
            raise ValueError(f"Filtros no reconocidos: {sorted(unknown)}")

        payload: dict = {
            "draw": 1,
            "columns": COLUMNS,
            "order": [{"column": order_col, "dir": order_dir}],
            "start": start,
            "length": length,
            "search": {"value": "", "regex": False},
        }
        payload.update({key: filters.get(key) for key in FILTER_KEYS})
        return self._post_json("contract/search", payload)

    def amendments(self, contract_number: str, entity_id) -> dict:
        """Enmiendas de un contrato.

        Se identifica por (numero, entidad), no por ContractId: los numeros de contrato se
        repiten entre agencias, asi que el par es la llave real del sistema.
        """
        return self._post_json(
            "contract/getamendments",
            {"ContractNumber": contract_number, "EntityId": entity_id},
        )

    def entities(self, name: str = "", page: int = 1, page_size: int = 100) -> dict:
        """Catalogo de agencias."""
        return self._get_json(
            "entity/findby", {"name": name, "pageIndex": page, "pageSize": page_size}
        )

    def services(
        self,
        name: str = "",
        service_group_id=None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict:
        """Catalogo de tipos de servicio."""
        return self._get_json(
            "service/findby",
            {
                "name": name,
                "serviceGroupId": service_group_id or "",
                "pageIndex": page,
                "pageSize": page_size,
            },
        )

    def _get_json(self, path: str, params: dict) -> dict:
        url = urllib.parse.urljoin(BASE, path) + "?" + urllib.parse.urlencode(params)

        def build() -> urllib.request.Request:
            return urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "__RequestVerificationToken": self.token,
                    "Referer": BASE,
                },
            )

        return self._fetch_json(build)
