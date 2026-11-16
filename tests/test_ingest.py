"""Pruebas de la proteccion de ingest.py y enrich.py contra descargas malas.

No tocan la red: el portal se sustituye por un cliente falso y los archivos van a un
directorio temporal.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import _ruta  # noqa: F401

import enrich
import ingest
from ocpr_client import PortalError

HOY = date(2026, 11, 15)
ANO = "2026-27"
MES = "2026-08"


class FakeClient:
    """Imita OcprClient.search para un portal con `disponibles` contratos por mes.

    `entrega` limita cuantas filas entrega de verdad (para simular una pagina vacia a
    mitad de camino). `anuncia` cambia el recordsFiltered. `falla` hace que lance.
    """

    def __init__(self, disponibles: dict[str, int], entrega: dict[str, int] | None = None,
                 anuncia: dict[str, int] | None = None, falla: set[str] | None = None):
        self.disponibles = disponibles
        self.entrega = entrega or {}
        self.anuncia = anuncia or {}
        self.falla = falla or set()
        self.pedidos: list[str] = []

    @staticmethod
    def _mes(filters) -> str:
        dia, mes, ano = filters["DateOfGrantFrom"].split("/")
        return f"{ano}-{mes}"

    def search(self, start=0, length=100, **filters):
        mes = self._mes(filters)
        if mes in self.falla:
            raise PortalError("portal caido (simulado)")
        total = self.disponibles.get(mes, 0)
        anunciado = self.anuncia.get(mes, total)
        if length == 1:
            self.pedidos.append(mes)
            return {"recordsFiltered": anunciado, "data": []}
        tope = min(total, self.entrega.get(mes, total))
        filas = [{"ContractId": i, "mes": mes} for i in range(start, min(start + length, tope))]
        return {"recordsFiltered": anunciado, "data": filas}


def escribir_mes(raiz: Path, mes: str, filas: int) -> Path:
    destino = raiz / ANO / f"{mes}.jsonl"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        "".join(json.dumps({"ContractId": i, "viejo": True}) + "\n" for i in range(filas)),
        encoding="utf-8",
    )
    return destino


class SuspiciousDropTest(unittest.TestCase):
    def test_crecer_o_quedar_igual_es_normal(self):
        self.assertIsNone(ingest.suspicious_drop(7000, 7000))
        self.assertIsNone(ingest.suspicious_drop(7000, 7350))
        self.assertIsNone(ingest.suspicious_drop(0, 0))
        self.assertIsNone(ingest.suspicious_drop(0, 500))

    def test_baja_pequena_se_tolera(self):
        self.assertIsNone(ingest.suspicious_drop(7000, 6900))   # 1.4 %
        self.assertIsNone(ingest.suspicious_drop(20, 16))        # 4 filas en un mes chico

    def test_baja_grande_se_rechaza(self):
        self.assertIsNotNone(ingest.suspicious_drop(7000, 6000))
        self.assertIsNotNone(ingest.suspicious_drop(7000, 6859))  # 141 > 2 % (140)
        self.assertIsNotNone(ingest.suspicious_drop(20, 14))

    def test_vacio_nunca_reemplaza_a_algo(self):
        self.assertIsNotNone(ingest.suspicious_drop(7000, 0))
        self.assertIsNotNone(ingest.suspicious_drop(3, 0))


class IngestGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        parche = mock.patch.object(ingest, "RAW_DIR", self.raiz)
        parche.start()
        self.addCleanup(parche.stop)
        self.addCleanup(self.tmp.cleanup)

    def correr(self, cliente, **kwargs):
        problemas: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            total = ingest.ingest_fiscal_year(
                cliente, ANO, refresh=True, problems=problemas, today=HOY, **kwargs
            )
        return total, problemas

    def test_resultado_truncado_no_reemplaza_el_mes_bueno(self):
        archivo = escribir_mes(self.raiz, MES, 2500)
        antes = archivo.read_bytes()
        # El portal anuncia 2,600 pero deja de entregar despues de la primera pagina.
        cliente = FakeClient({MES: 2600}, entrega={MES: 1000})
        total, problemas = self.correr(cliente)
        self.assertEqual(archivo.read_bytes(), antes)
        self.assertEqual(len(problemas), 1)
        self.assertIn(MES, problemas[0])
        self.assertEqual(total, 2500)

    def test_resultado_vacio_no_reemplaza_el_mes_bueno(self):
        archivo = escribir_mes(self.raiz, MES, 2500)
        antes = archivo.read_bytes()
        cliente = FakeClient({MES: 0})
        _, problemas = self.correr(cliente)
        self.assertEqual(archivo.read_bytes(), antes)
        self.assertEqual(len(problemas), 1)
        self.assertIn("vacio", problemas[0])

    def test_resultado_completo_pero_mucho_menor_no_reemplaza(self):
        archivo = escribir_mes(self.raiz, MES, 2500)
        antes = archivo.read_bytes()
        cliente = FakeClient({MES: 1200})
        _, problemas = self.correr(cliente)
        self.assertEqual(archivo.read_bytes(), antes)
        self.assertEqual(len(problemas), 1)

    def test_baja_grande_se_acepta_solo_si_se_pide(self):
        archivo = escribir_mes(self.raiz, MES, 2500)
        cliente = FakeClient({MES: 1200})
        _, problemas = self.correr(cliente, accept_drops=True)
        self.assertEqual(problemas, [])
        self.assertEqual(ingest.count_lines(archivo), 1200)

    def test_mes_que_crece_si_se_reemplaza(self):
        archivo = escribir_mes(self.raiz, MES, 2500)
        cliente = FakeClient({MES: 2650})
        total, problemas = self.correr(cliente)
        self.assertEqual(problemas, [])
        self.assertEqual(ingest.count_lines(archivo), 2650)
        self.assertNotIn("viejo", archivo.read_text(encoding="utf-8"))
        self.assertEqual(total, 2650)

    def test_fallo_del_portal_conserva_el_archivo_y_sigue_con_los_demas(self):
        archivo = escribir_mes(self.raiz, MES, 2500)
        antes = archivo.read_bytes()
        cliente = FakeClient({MES: 2600, "2026-09": 40}, falla={MES})
        _, problemas = self.correr(cliente)
        self.assertEqual(archivo.read_bytes(), antes)
        self.assertEqual(len(problemas), 1)
        # El mes siguiente se bajo de todos modos.
        self.assertEqual(ingest.count_lines(self.raiz / ANO / "2026-09.jsonl"), 40)

    def test_mes_nuevo_incompleto_no_deja_archivo(self):
        cliente = FakeClient({MES: 2600}, entrega={MES: 1000})
        _, problemas = self.correr(cliente)
        self.assertFalse((self.raiz / ANO / f"{MES}.jsonl").exists())
        self.assertEqual(len(problemas), 1)

    def test_no_quedan_temporales_ni_meses_futuros(self):
        cliente = FakeClient({MES: 30})
        self.correr(cliente)
        nombres = sorted(p.name for p in (self.raiz / ANO).iterdir())
        self.assertNotIn("2026-12.jsonl", nombres)   # diciembre de 2026 es futuro para HOY
        self.assertFalse([n for n in nombres if n.endswith(".tmp")])

    def test_sin_refresh_no_se_vuelve_a_pedir_lo_guardado(self):
        escribir_mes(self.raiz, MES, 2500)
        cliente = FakeClient({MES: 9999})
        with contextlib.redirect_stdout(io.StringIO()):
            ingest.ingest_fiscal_year(cliente, ANO, refresh=False, problems=[], today=HOY)
        self.assertNotIn(MES, cliente.pedidos)

    def test_main_termina_con_codigo_1_si_hubo_problemas(self):
        escribir_mes(self.raiz, MES, 2500)
        cliente = FakeClient({MES: 0})
        with mock.patch.object(ingest, "OcprClient", return_value=cliente), \
             mock.patch("sys.argv", ["ingest.py", "--refresh", ANO]), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as salida:
                ingest.main()
        self.assertEqual(salida.exception.code, 1)
        self.assertIn(MES, err.getvalue())

    def test_refresh_years_baja_cada_mes_una_sola_vez(self):
        # Cache fria: no hay nada en disco. Un ano viejo y uno reciente en la misma pasada.
        cliente = FakeClient({})
        with mock.patch.object(ingest, "OcprClient", return_value=cliente), \
             mock.patch("sys.argv", ["ingest.py", "2025-26", ANO, "--refresh-years", ANO]), \
             contextlib.redirect_stdout(io.StringIO()):
            ingest.main()
        self.assertEqual(len(cliente.pedidos), len(set(cliente.pedidos)))
        self.assertIn("2025-07", cliente.pedidos)
        self.assertIn("2026-07", cliente.pedidos)


class FiscalYearTest(unittest.TestCase):
    def test_doce_meses_de_julio_a_junio(self):
        meses = ingest.fiscal_year_months("2023-24")
        self.assertEqual(len(meses), 12)
        self.assertEqual(meses[0], (date(2023, 7, 1), date(2023, 7, 31)))
        self.assertEqual(meses[7], (date(2024, 2, 1), date(2024, 2, 29)))  # bisiesto
        self.assertEqual(meses[-1], (date(2024, 6, 1), date(2024, 6, 30)))


class EnrichGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        parche = mock.patch.object(enrich, "TAGS_DIR", self.raiz)
        parche.start()
        self.addCleanup(parche.stop)
        self.addCleanup(self.tmp.cleanup)
        self.archivo = self.raiz / ANO / f"{MES}.json"
        self.archivo.parent.mkdir(parents=True)
        self.archivo.write_text(json.dumps({
            "forms": {str(i): 5 for i in range(1000)},
            "funds": {str(i): 2 for i in range(800)},
        }), encoding="utf-8")

    def correr(self, etiquetas):
        problemas: list[str] = []

        def falso_tag_month(client, first, last):
            if f"{first:%Y-%m}" != MES:
                return {"forms": {}, "funds": {}}
            if isinstance(etiquetas, Exception):
                raise etiquetas
            return etiquetas

        with mock.patch.object(enrich, "tag_month", falso_tag_month), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            enrich.enrich_fiscal_year(object(), ANO, refresh=True, problems=problemas, today=HOY)
        return problemas

    def test_etiquetas_vacias_no_reemplazan_las_buenas(self):
        antes = self.archivo.read_bytes()
        problemas = self.correr({"forms": {}, "funds": {}})
        self.assertEqual(self.archivo.read_bytes(), antes)
        self.assertEqual(len(problemas), 1)

    def test_baja_grande_en_fondos_no_reemplaza(self):
        antes = self.archivo.read_bytes()
        problemas = self.correr({"forms": {str(i): 5 for i in range(1000)},
                                 "funds": {str(i): 2 for i in range(300)}})
        self.assertEqual(self.archivo.read_bytes(), antes)
        self.assertEqual(len(problemas), 1)

    def test_descarga_incompleta_no_reemplaza(self):
        antes = self.archivo.read_bytes()
        problemas = self.correr(ingest.IncompleteDownload("anuncio 900 y entrego 100"))
        self.assertEqual(self.archivo.read_bytes(), antes)
        self.assertEqual(len(problemas), 1)

    def test_etiquetas_que_crecen_si_reemplazan(self):
        problemas = self.correr({"forms": {str(i): 5 for i in range(1100)},
                                 "funds": {str(i): 2 for i in range(900)}})
        self.assertEqual(problemas, [])
        guardado = json.loads(self.archivo.read_text(encoding="utf-8"))
        self.assertEqual(len(guardado["forms"]), 1100)

    def test_collect_ids_detecta_pagina_vacia_a_mitad(self):
        cliente = FakeClient({MES: 2600}, entrega={MES: 1000})
        with self.assertRaises(ingest.IncompleteDownload):
            enrich.collect_ids(cliente, "01/08/2026", "31/08/2026")


if __name__ == "__main__":
    unittest.main()
