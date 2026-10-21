"""Cliente del API de consulta del Registro de Contratos (Oficina de la Contralora de PR).

El portal consultacontratos.ocpr.gov.pr es una app ASP.NET con DataTables server-side.
Expone JSON sobre POST, protegido por un token anti-forgery que hay que extraer del HTML
y reenviar junto con las cookies de sesion.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import ssl
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

    def __init__(self, delay: float = 1.5, timeout: int = 60):
        self.delay = delay
        self.timeout = timeout
        self.token: str | None = None
        self._last_request = 0.0
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPSHandler(context=_ssl_context()),
        )

    def _throttle(self) -> None:
        """Nunca mas de una peticion cada `delay` segundos: es un servidor publico."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
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
            raise RuntimeError(
                "No se encontro __RequestVerificationToken: el portal cambio de estructura."
            )
        self.token = match.group(1)
        return self.token

    def _post_json(self, path: str, payload: dict) -> dict:
        if self.token is None:
            self.open_session()
        self._throttle()
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
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
        with self._opener.open(req, timeout=self.timeout) as resp:
            # utf-8-sig, no utf-8: el servidor devuelve BOM de forma intermitente y
            # json.loads lo rechaza con "Unexpected UTF-8 BOM". Rompio la corrida del
            # 2026-10-21 despues de 50 minutos. Sin BOM se comporta igual que utf-8.
            return json.loads(self._read(resp).decode("utf-8-sig", "replace"))

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
        if self.token is None:
            self.open_session()
        self._throttle()
        url = urllib.parse.urljoin(BASE, path) + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "__RequestVerificationToken": self.token,
                "Referer": BASE,
            },
        )
        with self._opener.open(req, timeout=self.timeout) as resp:
            return json.loads(self._read(resp).decode("utf-8-sig", "replace"))
