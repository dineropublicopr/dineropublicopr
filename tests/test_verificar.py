"""Pruebas del control de cordura que decide si una corrida se publica."""

from __future__ import annotations

import copy
import unittest

import _ruta  # noqa: F401

import verificar

ANTERIOR = {
    "contratos": 273_818,
    "monto_total": 97_561_644_057.87,
    "anos_fiscales": [
        {"ano": "2023-24", "contratos": 80_137, "monto": 23_332_537_793.39},
        {"ano": "2024-25", "contratos": 79_913, "monto": 34_312_732_052.27},
        {"ano": "2025-26", "contratos": 78_325, "monto": 31_812_974_658.14},
        {"ano": "2026-27", "contratos": 35_443, "monto": 8_103_399_554.07},
    ],
}


def con(cambios: dict[str, int], **extra) -> dict:
    """Copia de ANTERIOR con otros conteos por ano y el total recalculado."""
    nuevo = copy.deepcopy(ANTERIOR)
    for fila in nuevo["anos_fiscales"]:
        if fila["ano"] in cambios:
            fila["contratos"] = cambios[fila["ano"]]
    nuevo["anos_fiscales"] = [f for f in nuevo["anos_fiscales"] if f["contratos"] is not None]
    nuevo["contratos"] = sum(f["contratos"] for f in nuevo["anos_fiscales"])
    nuevo.update(extra)
    return nuevo


class VerificarTest(unittest.TestCase):
    def test_sin_cambios_pasa(self):
        self.assertEqual(verificar.check(copy.deepcopy(ANTERIOR), ANTERIOR), [])

    def test_crecimiento_semanal_normal_pasa(self):
        self.assertEqual(verificar.check(con({"2026-27": 36_900, "2025-26": 78_340}), ANTERIOR), [])

    def test_ano_fiscal_nuevo_pasa(self):
        nuevo = copy.deepcopy(ANTERIOR)
        nuevo["anos_fiscales"].append({"ano": "2027-28", "contratos": 900, "monto": 1.0})
        nuevo["contratos"] += 900
        self.assertEqual(verificar.check(nuevo, ANTERIOR), [])

    def test_baja_minima_por_correcciones_pasa(self):
        self.assertEqual(verificar.check(con({"2025-26": 78_300}), ANTERIOR), [])

    def test_perder_un_mes_falla(self):
        # Unos 7,000 contratos menos: con el umbral viejo de 50,000 esto se publicaba.
        problemas = verificar.check(con({"2026-27": 28_443}), ANTERIOR)
        self.assertTrue(problemas)
        self.assertTrue(any("2026-27" in p for p in problemas))

    def test_perder_un_ano_entero_falla(self):
        problemas = verificar.check(con({"2024-25": None}), ANTERIOR)
        self.assertTrue(any("desaparecio" in p for p in problemas))
        self.assertTrue(any("total bajo" in p for p in problemas))

    def test_el_caso_que_el_umbral_viejo_dejaba_pasar(self):
        # 193,905 contratos: muy por encima de 50,000 y aun asi falta un ano.
        nuevo = con({"2024-25": None})
        self.assertGreater(nuevo["contratos"], 50_000)
        self.assertTrue(verificar.check(nuevo, ANTERIOR))

    def test_baja_de_un_ano_dentro_del_total_tolerado_tambien_falla(self):
        # 2026-27 pierde 5 % (1,772) mientras otro ano sube: el total casi no cambia.
        nuevo = con({"2026-27": 33_671, "2025-26": 79_900})
        problemas = verificar.check(nuevo, ANTERIOR)
        self.assertEqual(len(problemas), 1)
        self.assertIn("2026-27", problemas[0])

    def test_monto_que_se_desploma_falla(self):
        nuevo = copy.deepcopy(ANTERIOR)
        nuevo["monto_total"] = ANTERIOR["monto_total"] * 0.5
        self.assertTrue(any("monto" in p for p in verificar.check(nuevo, ANTERIOR)))

    def test_total_que_no_cuadra_con_los_anos_falla(self):
        nuevo = copy.deepcopy(ANTERIOR)
        nuevo["contratos"] += 5_000
        self.assertTrue(any("no cuadra" in p for p in verificar.check(nuevo, ANTERIOR)))

    def test_resumen_vacio_o_roto_falla(self):
        self.assertTrue(verificar.check({}, ANTERIOR))
        self.assertTrue(verificar.check({"contratos": "muchos"}, ANTERIOR))
        self.assertTrue(verificar.check({"contratos": 0, "anos_fiscales": []}, ANTERIOR))

    def test_sin_resumen_anterior_solo_aplica_el_minimo(self):
        self.assertEqual(verificar.check(copy.deepcopy(ANTERIOR), None), [])
        chico = {"contratos": 1_200, "anos_fiscales": [{"ano": "2026-27", "contratos": 1_200}]}
        self.assertTrue(verificar.check(chico, None))

    def test_permitir_baja_acepta_la_baja_pero_no_un_resumen_roto(self):
        self.assertEqual(verificar.check(con({"2026-27": 28_443}), ANTERIOR, permitir_baja=True), [])
        roto = copy.deepcopy(ANTERIOR)
        roto["contratos"] += 5_000
        self.assertTrue(verificar.check(roto, ANTERIOR, permitir_baja=True))
        chico = {"contratos": 1_200, "anos_fiscales": [{"ano": "2026-27", "contratos": 1_200}]}
        self.assertTrue(verificar.check(chico, ANTERIOR, permitir_baja=True))


if __name__ == "__main__":
    unittest.main()
