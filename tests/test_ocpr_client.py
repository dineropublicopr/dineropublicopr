"""Pruebas de los reintentos del cliente del portal. Sin red y sin esperas reales."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
import urllib.error

import _ruta  # noqa: F401

import ocpr_client
from ocpr_client import OcprClient, PortalError

HTML_SESION = '<input name="__RequestVerificationToken" type="hidden" value="tok-{n}">'


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Devuelve, en orden, las respuestas programadas para las peticiones de datos.

    Las peticiones a la pagina de inicio (abrir sesion) siempre funcionan y entregan un
    token distinto cada vez, salvo que `sesion_falla` diga lo contrario.
    """

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.sesiones = 0
        self.datos = 0
        self.tokens_usados = []

    def open(self, req, timeout=None):
        if req.get_method() == "GET" and req.full_url == ocpr_client.BASE:
            self.sesiones += 1
            return FakeResponse(HTML_SESION.format(n=self.sesiones).encode())
        self.datos += 1
        self.tokens_usados.append(req.get_header("__requestverificationtoken"))
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return FakeResponse(siguiente)


def http_error(code):
    return urllib.error.HTTPError(ocpr_client.BASE, code, "simulado", {}, None)


OK = json.dumps({"recordsFiltered": 3, "data": []}).encode()


class RetryTest(unittest.TestCase):
    def cliente(self, respuestas):
        c = OcprClient(delay=0)
        c._opener = FakeOpener(respuestas)
        self.esperas = []
        c._sleep = self.esperas.append
        return c

    def buscar(self, c):
        with contextlib.redirect_stderr(io.StringIO()):
            return c.search(start=0, length=1)

    def test_respuesta_buena_no_espera(self):
        c = self.cliente([OK])
        self.assertEqual(self.buscar(c)["recordsFiltered"], 3)
        self.assertEqual(self.esperas, [])
        self.assertEqual(c._opener.sesiones, 1)

    def test_reintenta_502_y_timeout_con_espera_creciente(self):
        c = self.cliente([http_error(502), TimeoutError("lento"), OK])
        self.assertEqual(self.buscar(c)["recordsFiltered"], 3)
        self.assertEqual(self.esperas, [5, 15])
        self.assertEqual(c._opener.datos, 3)

    def test_reintenta_error_de_red(self):
        c = self.cliente([urllib.error.URLError("sin conexion"), ConnectionResetError(), OK])
        self.assertEqual(self.buscar(c)["recordsFiltered"], 3)
        self.assertEqual(self.esperas, [5, 15])

    def test_se_rinde_tras_cuatro_intentos(self):
        c = self.cliente([http_error(503)] * 4)
        with self.assertRaises(PortalError):
            self.buscar(c)
        self.assertEqual(self.esperas, [5, 15, 45])
        self.assertEqual(c._opener.datos, 4)

    def test_html_en_vez_de_json_renueva_la_sesion(self):
        c = self.cliente([b"<html>inicie sesion</html>", OK])
        self.assertEqual(self.buscar(c)["recordsFiltered"], 3)
        self.assertEqual(c._opener.sesiones, 2)
        self.assertEqual(c._opener.tokens_usados, ["tok-1", "tok-2"])

    def test_403_renueva_la_sesion(self):
        c = self.cliente([http_error(403), OK])
        self.assertEqual(self.buscar(c)["recordsFiltered"], 3)
        self.assertEqual(c._opener.sesiones, 2)

    def test_un_400_no_se_reintenta(self):
        c = self.cliente([http_error(400), OK])
        with self.assertRaises(urllib.error.HTTPError):
            self.buscar(c)
        self.assertEqual(self.esperas, [])
        self.assertEqual(c._opener.datos, 1)

    def test_bom_intermitente_se_lee_bien(self):
        c = self.cliente([b"\xef\xbb\xbf" + OK])
        self.assertEqual(self.buscar(c)["recordsFiltered"], 3)

    def test_get_tambien_reintenta(self):
        c = self.cliente([http_error(500), json.dumps({"Results": []}).encode()])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(c.entities(), {"Results": []})
        self.assertEqual(self.esperas, [5])


if __name__ == "__main__":
    unittest.main()
