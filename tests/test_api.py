"""Pruebas del API (functions/api/v1/[[route]].js) sin Cloudflare.

El manejador es JavaScript, asi que se ejecuta con Node a traves de tests/api_harness.mjs,
que le pasa peticiones de verdad y una base falsa. Si no hay Node instalado, estas pruebas
se saltan. Lo que NO cubren: el comportamiento de D1 real (sus limites propios) ni el
enrutado de Cloudflare Pages.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
ROUTE = ROOT / "functions" / "api" / "v1" / "[[route]].js"
HARNESS = Path(__file__).resolve().parent / "api_harness.mjs"

AGENCIA_LARGA = "Autoridad de Acueductos y Alcantarillados de Puerto Rico"

# (nombre, ruta, base). Sin base indicada se usa la que solo cuenta consultas.
CASOS = [
    ("raiz", "/api/v1/", None),
    ("sin_filtros", "/api/v1/contratos", None),
    ("ruta_inexistente", "/api/v1/no-existe", None),
    ("post", "/api/v1/contratos", None),
    ("metodo_abc", "/api/v1/contratos?metodo=abc", None),
    ("fondo_decimal", "/api/v1/contratos?fondo=1.5", None),
    ("min_abc", "/api/v1/contratos?min=abc", None),
    ("max_nan", "/api/v1/contratos?max=NaN", None),
    ("min_infinito", "/api/v1/contratos?min=Infinity", None),
    ("min_exponente", "/api/v1/contratos?min=1e9", None),
    ("min_mayor_que_max", "/api/v1/contratos?min=500&max=100", None),
    ("limite_abc", "/api/v1/contratos?limite=abc", None),
    ("limite_negativo", "/api/v1/contratos?limite=-5", None),
    ("limite_enorme", "/api/v1/contratos?limite=99999", None),
    ("pagina_cero", "/api/v1/contratos?pagina=0", None),
    ("pagina_abc", "/api/v1/contratos?pagina=dos", None),
    ("desde_malo", "/api/v1/contratos?desde=ayer", None),
    ("hasta_imposible", "/api/v1/contratos?hasta=2026-02-31", None),
    ("desde_despues_de_hasta", "/api/v1/contratos?desde=2026-05-01&hasta=2026-01-01", None),
    ("ano_malo", "/api/v1/contratos?ano=2025-26'%20OR%201=1--", None),
    ("orden_inyeccion", "/api/v1/contratos?orden=monto;DROP%20TABLE%20contracts", None),
    ("dir_malo", "/api/v1/contratos?dir=arriba", None),
    ("agencia_49", "/api/v1/contratos?agencia=" + "a" * 49, None),
    ("agencia_120", "/api/v1/contratos?agencia=" + "a" * 120, None),
    ("agencia_121", "/api/v1/contratos?agencia=" + "a" * 121, None),
    ("contratista_121", "/api/v1/contratos?contratista=" + "b" * 121, None),
    ("contratistas_q_121", "/api/v1/contratistas?q=" + "c" * 121, None),
    ("contratistas_limite_abc", "/api/v1/contratistas?limite=abc", None),
    ("validos", "/api/v1/contratos?metodo=5&fondo=1&min=250000&max=1000000.50"
                "&desde=2025-07-01&hasta=2026-06-30&ano=2025-26&orden=monto&dir=asc"
                "&limite=10&pagina=2", None),
    ("sin_base", "/api/v1/resumen", "ninguna"),
    ("base_rota", "/api/v1/contratos?agencia=salud", "roto"),
    ("base_rota_resumen", "/api/v1/resumen", "roto"),
    ("sql_agencia_larga", "/api/v1/contratos?agencia=" + quote(AGENCIA_LARGA), "sqlite"),
    ("sql_agencia_minusculas", "/api/v1/contratos?agencia=" + quote(AGENCIA_LARGA.lower()), "sqlite"),
    ("sql_contratista_largo", "/api/v1/contratos?contratista="
        + quote("Empresa Ficticia de Construccion y Servicios Generales del Caribe"), "sqlite"),
    ("sql_porciento_literal", "/api/v1/contratos?agencia=%25", "sqlite"),
    ("sql_min", "/api/v1/contratos?min=250000&orden=monto&dir=asc", "sqlite"),
    ("sql_metodo", "/api/v1/contratos?metodo=5", "sqlite"),
    ("sql_contratistas_q", "/api/v1/contratistas?q=inventada", "sqlite"),
    ("sql_detalle", "/api/v1/contratos/1", "sqlite"),
    ("sql_detalle_no_existe", "/api/v1/contratos/999", "sqlite"),
    ("sql_resumen", "/api/v1/resumen", "sqlite"),
]


@unittest.skipUnless(shutil.which("node"), "Node no esta instalado")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        casos = [
            {"nombre": nombre, "ruta": ruta, "db": db,
             "metodo": "POST" if nombre == "post" else "GET"}
            for nombre, ruta, db in CASOS
        ]
        proc = subprocess.run(
            ["node", str(HARNESS), str(ROUTE)],
            input=json.dumps(casos).encode("utf-8"),
            capture_output=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError("el arnes de Node fallo:\n" + proc.stderr.decode("utf-8", "replace"))
        salida = json.loads(proc.stdout.decode("utf-8"))
        cls.hay_sqlite = salida["sqlite"]
        cls.r = {item["nombre"]: item for item in salida["resultados"]}

    def sql(self, nombre):
        item = self.r[nombre]
        if item.get("omitido"):
            self.skipTest("esta version de Node no trae node:sqlite")
        return item

    # ── Respuestas buenas ────────────────────────────────────────────────
    def test_respuestas_buenas_se_cachean_una_hora(self):
        for nombre in ("raiz", "sin_filtros", "validos"):
            with self.subTest(nombre):
                self.assertEqual(self.r[nombre]["estado"], 200)
                self.assertEqual(self.r[nombre]["cache"], "public, max-age=3600")

    def test_parametros_validos_llegan_a_la_base(self):
        item = self.r["validos"]
        self.assertEqual(item["consultas"], 2)
        self.assertEqual(item["cuerpo"]["limite"], 10)
        self.assertEqual(item["cuerpo"]["pagina"], 2)

    def test_limite_enorme_se_ajusta_al_maximo(self):
        self.assertEqual(self.r["limite_enorme"]["estado"], 200)
        self.assertEqual(self.r["limite_enorme"]["cuerpo"]["limite"], 500)

    # ── Validacion: 400, sin cache, sin tocar la base ────────────────────
    def test_numeros_invalidos_dan_400(self):
        for nombre in ("metodo_abc", "fondo_decimal", "min_abc", "max_nan", "min_infinito",
                       "min_exponente", "min_mayor_que_max", "limite_abc", "limite_negativo",
                       "pagina_cero", "pagina_abc", "contratistas_limite_abc"):
            with self.subTest(nombre):
                item = self.r[nombre]
                self.assertEqual(item["estado"], 400, item["texto"])
                self.assertIn("error", item["cuerpo"])
                self.assertNotIn("resultados", item["cuerpo"])
                self.assertEqual(item["consultas"], 0, "no debe consultar la base")

    def test_fechas_ano_orden_y_dir_invalidos_dan_400(self):
        for nombre in ("desde_malo", "hasta_imposible", "desde_despues_de_hasta", "ano_malo",
                       "orden_inyeccion", "dir_malo"):
            with self.subTest(nombre):
                self.assertEqual(self.r[nombre]["estado"], 400, self.r[nombre]["texto"])
                self.assertEqual(self.r[nombre]["consultas"], 0)

    def test_el_mensaje_de_400_nombra_el_parametro(self):
        self.assertIn("metodo", self.r["metodo_abc"]["cuerpo"]["error"])
        self.assertIn("min", self.r["min_abc"]["cuerpo"]["error"])
        self.assertIn("agencia", self.r["agencia_121"]["cuerpo"]["error"])

    def test_texto_de_49_a_120_caracteres_se_acepta(self):
        # Antes, 49 caracteres o mas daban 500 por el limite de LIKE en D1.
        for nombre in ("agencia_49", "agencia_120"):
            with self.subTest(nombre):
                self.assertEqual(self.r[nombre]["estado"], 200, self.r[nombre]["texto"])

    def test_texto_demasiado_largo_da_400(self):
        for nombre in ("agencia_121", "contratista_121", "contratistas_q_121"):
            with self.subTest(nombre):
                self.assertEqual(self.r[nombre]["estado"], 400)
                self.assertEqual(self.r[nombre]["consultas"], 0)

    def test_ningun_error_se_cachea(self):
        for nombre, item in self.r.items():
            if item.get("omitido") or item["estado"] < 400:
                continue
            with self.subTest(nombre):
                self.assertEqual(item["cache"], "no-store")

    def test_404_y_405(self):
        self.assertEqual(self.r["ruta_inexistente"]["estado"], 404)
        self.assertEqual(self.r["post"]["estado"], 405)

    # ── Errores internos: nunca sale el texto de la base ─────────────────
    def test_error_interno_no_filtra_detalles(self):
        for nombre in ("base_rota", "base_rota_resumen"):
            with self.subTest(nombre):
                item = self.r[nombre]
                self.assertEqual(item["estado"], 500)
                self.assertEqual(item["cache"], "no-store")
                for secreto in ("D1_ERROR", "SQLITE", "tabla_secreta", "no such table"):
                    self.assertNotIn(secreto, item["texto"])
                # El detalle si queda en el log para quien opera el sitio.
                self.assertIn("D1_ERROR", item["log"])

    def test_sin_base_da_503_generico(self):
        item = self.r["sin_base"]
        self.assertEqual(item["estado"], 503)
        self.assertEqual(item["cache"], "no-store")
        self.assertNotIn("D1", item["texto"])
        self.assertNotIn("binding", item["texto"])

    # ── SQL de verdad sobre SQLite en memoria (si Node lo trae) ──────────
    def test_sql_nombre_largo_de_agencia_encuentra_sus_contratos(self):
        item = self.sql("sql_agencia_larga")
        self.assertEqual(item["estado"], 200, item["texto"])
        self.assertEqual(item["cuerpo"]["total"], 2)
        self.assertEqual(self.sql("sql_agencia_minusculas")["cuerpo"]["total"], 2)

    def test_sql_contratista_de_mas_de_48_caracteres(self):
        item = self.sql("sql_contratista_largo")
        self.assertEqual(item["estado"], 200, item["texto"])
        self.assertEqual(item["cuerpo"]["total"], 1)
        self.assertIn("Empresa Ficticia", item["cuerpo"]["resultados"][0]["contratistas"])

    def test_sql_el_porciento_ya_no_es_comodin(self):
        # Con LIKE, agencia=% devolvia todos los contratos. Ahora busca el caracter.
        self.assertEqual(self.sql("sql_porciento_literal")["cuerpo"]["total"], 1)

    def test_sql_filtros_numericos_y_orden(self):
        item = self.sql("sql_min")
        self.assertEqual([r["contract_id"] for r in item["cuerpo"]["resultados"]], [3, 1])
        self.assertEqual(self.sql("sql_metodo")["cuerpo"]["total"], 1)

    def test_sql_otras_rutas(self):
        self.assertEqual(len(self.sql("sql_contratistas_q")["cuerpo"]), 1)
        self.assertEqual(self.sql("sql_contratistas_q")["cuerpo"][0]["contratos"], 2)
        self.assertEqual(self.sql("sql_detalle")["cuerpo"]["contract_number"], "2026-000001")
        self.assertEqual(self.sql("sql_detalle_no_existe")["estado"], 404)
        self.assertEqual(self.sql("sql_resumen")["cuerpo"]["contratos"], 3)


if __name__ == "__main__":
    unittest.main()
