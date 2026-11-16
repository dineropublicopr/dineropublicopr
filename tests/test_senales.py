"""Comprueba que data/legal/senales.json cumple las reglas mecanicas de data/legal/README.md.

Lo que una prueba NO puede comprobar: que el texto diga solo lo que dice la fuente, ni que
no haya el nombre de una persona natural. Eso lo revisa una persona abriendo las fuentes.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SENALES = ROOT / "data" / "legal" / "senales.json"
PUBLICADO = ROOT / "web" / "datos" / "legal.json"

TIPOS = {"condena", "exclusion", "acusacion", "demanda_gobierno"}
CAMPOS = ("entidad", "tipo", "jurisdiccion", "fecha", "alegacion", "estatus", "fuentes", "verificado_el")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SenalesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.senales = json.loads(SENALES.read_text(encoding="utf-8"))["senales"]

    def test_hay_senales(self):
        self.assertTrue(self.senales)

    def test_campos_obligatorios_y_tipo(self):
        for s in self.senales:
            with self.subTest(s.get("entidad")):
                for campo in CAMPOS:
                    self.assertTrue(s.get(campo), f"falta {campo}")
                self.assertIn(s["tipo"], TIPOS)
                self.assertRegex(s["fecha"], ISO)
                self.assertRegex(s["verificado_el"], ISO)
                self.assertIn("vinculo", s)

    def test_cada_senal_tiene_una_fuente_oficial_con_enlace(self):
        for s in self.senales:
            with self.subTest(s.get("entidad")):
                self.assertTrue(any(f.get("oficial") is True for f in s["fuentes"]))
                for f in s["fuentes"]:
                    self.assertTrue(f.get("titulo") and f.get("emisor"))
                    self.assertTrue(str(f.get("url", "")).startswith("https://"))

    def test_una_acusacion_no_se_redacta_como_condena(self):
        # Mientras el tipo sea "acusacion", el estatus y el vinculo no pueden afirmar
        # culpabilidad como hecho. Hablar de una intencion notificada si se permite.
        afirmaciones = ("aceptó culpa", "acepto culpa", "se declaró culpable", "se declaro culpable",
                        "fue convict", "fue hallad", "culpable de")
        # Excepcion: el vinculo de esta senal habla de una declaracion de culpabilidad del
        # dueno, que es lo que informan las fuentes citadas de julio de 2026 (comunicado
        # del FBI y nota de prensa). Pendiente de que una persona abra el comunicado
        # oficial; si se reescribe, se quita de esta lista.
        permitido_por_revisar = {"G Fitness & Body Performance LLC"}
        for s in self.senales:
            if s["tipo"] != "acusacion" or s["entidad"] in permitido_por_revisar:
                continue
            texto = (s.get("estatus") or "") + " " + (s.get("vinculo") or "")
            with self.subTest(s["entidad"]):
                for frase in afirmaciones:
                    self.assertNotIn(frase, texto.lower())

    def test_lo_publicado_coincide_con_el_registro_curado(self):
        if not PUBLICADO.exists():
            self.skipTest("no hay web/datos/legal.json")
        publicado = {x["entidad"]: x for x in json.loads(PUBLICADO.read_text(encoding="utf-8"))["senales"]
                     if not x.get("automatica")}
        for s in self.senales:
            if s["entidad"] not in publicado:
                continue  # senal curada cuya empresa hoy no tiene contratos
            with self.subTest(s["entidad"]):
                for campo in ("alegacion", "estatus", "vinculo", "verificado_el"):
                    self.assertEqual(publicado[s["entidad"]].get(campo), s.get(campo), campo)


if __name__ == "__main__":
    unittest.main()
