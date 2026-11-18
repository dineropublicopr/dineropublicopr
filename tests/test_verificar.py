"""Pruebas del control de cordura que decide si una corrida se publica."""

from __future__ import annotations

import copy
import unittest
from datetime import date

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


# ---- Cabilderos, Legal y Conexiones ----

HOY = date(2026, 11, 18)

CABILDEROS = {
    "resumen": {"firmas": 4},
    "firmas": [
        {"nombre": f"Firma {n} LLC", "registros": [{"numero": f"2026Q1-0000{n}"}], "clientes": []}
        for n in range(1, 5)
    ],
    "senado": [{"nombre": f"Persona {n}"} for n in range(20)],
}
RED = {
    "nodos": [["f0", "f", "Firma 1 LLC", 0, {}], ["f1", "f", "Firma 2 LLC", 0, {}],
              ["f2", "f", "Firma 3 LLC", 0, {}], ["f3", "f", "Firma 4 LLC", 0, {}],
              ["c4", "c", "ACME INC", 100, {}], ["a5", "a", "Departamento de Salud", 100, {}]],
    "aristas": [["f0", "c4", "l", 100, 0, ["2026Q1-00001"]], ["c4", "a5", "k", 100, 1, ["2026-01-01", "2026-02-01"]]],
}
LEGAL = {
    "curadas_sin_contratos": 0,
    "senales": [{
        "entidad": "Empresa Ejemplo Inc.",
        "automatica": False,
        "verificado_el": "2026-11-01",
        "fuentes": [{"url": "https://ejemplo.test/comunicado"}],
        "contratista": {"clave": "EMPRESA EJEMPLO"},
    }],
}


def conexiones(**cambios):
    """check_conexiones sobre copias de los tres archivos buenos, con lo que se pida cambiado."""
    datos = {"cabilderos": copy.deepcopy(CABILDEROS), "red": copy.deepcopy(RED), "legal": copy.deepcopy(LEGAL)}
    extra = {}
    for clave, valor in cambios.items():
        if clave in datos:
            datos[clave] = valor
        else:
            extra[clave] = valor
    return verificar.check_conexiones(datos["cabilderos"], datos["red"], datos["legal"], hoy=HOY, **extra)


class VerificarConexionesTest(unittest.TestCase):
    def test_datos_buenos_pasan_sin_avisos(self):
        self.assertEqual(conexiones(cab_anterior=CABILDEROS, red_anterior=RED), ([], []))

    def test_sin_firmas_no_se_publica(self):
        vacio = {"resumen": {"firmas": 0}, "firmas": [], "senado": []}
        problemas, _ = conexiones(cabilderos=vacio)
        self.assertTrue(any("ninguna firma" in p for p in problemas))

    def test_el_resumen_tiene_que_cuadrar_con_la_lista(self):
        mal = copy.deepcopy(CABILDEROS)
        mal["resumen"]["firmas"] = 9
        problemas, _ = conexiones(cabilderos=mal)
        self.assertTrue(any("resumen" in p for p in problemas))

    def test_una_firma_sin_inscripcion_no_se_publica(self):
        mal = copy.deepcopy(CABILDEROS)
        mal["firmas"][0]["registros"] = []
        problemas, _ = conexiones(cabilderos=mal)
        self.assertTrue(any("sin inscripcion" in p for p in problemas))

    def test_perder_mas_de_la_mitad_de_las_firmas_detiene_la_corrida(self):
        antes = copy.deepcopy(CABILDEROS)
        antes["firmas"] = antes["firmas"] * 3  # 12 firmas publicadas, llegan 4
        problemas, _ = conexiones(cab_anterior=antes)
        self.assertTrue(any("bajaron de 12 a 4" in p for p in problemas))
        self.assertEqual(conexiones(cab_anterior=antes, permitir_baja=True)[0], [])

    def test_una_baja_normal_de_firmas_pasa(self):
        antes = copy.deepcopy(CABILDEROS)
        antes["firmas"] = antes["firmas"] + antes["firmas"][:2]  # 6 publicadas, llegan 4
        self.assertEqual(conexiones(cab_anterior=antes)[0], [])

    def test_el_senado_solo_crece(self):
        antes = copy.deepcopy(CABILDEROS)
        antes["senado"] = antes["senado"] * 2  # 40 publicadas, llegan 20
        problemas, _ = conexiones(cab_anterior=antes)
        self.assertTrue(any("Senado" in p for p in problemas))

    def test_mapa_vacio_no_se_publica(self):
        problemas, _ = conexiones(red={"nodos": [], "aristas": []})
        self.assertTrue(any("vacio" in p for p in problemas))

    def test_conexion_hacia_un_punto_que_no_existe(self):
        mal = copy.deepcopy(RED)
        mal["aristas"].append(["c4", "a99", "k", 1, 1])
        problemas, _ = conexiones(red=mal)
        self.assertTrue(any("no existe" in p for p in problemas))

    def test_el_mapa_dibuja_las_mismas_firmas_que_la_lista(self):
        mal = copy.deepcopy(RED)
        mal["nodos"] = [n for n in mal["nodos"] if n[0] != "f3"]
        problemas, _ = conexiones(red=mal)
        self.assertTrue(any("dibuja 3 firmas" in p for p in problemas))

    def test_aristas_sin_el_campo_de_fechas_siguen_valiendo(self):
        # El sexto campo es opcional: asi son los red.json anteriores y las aristas p y s.
        viejo = copy.deepcopy(RED)
        viejo["aristas"] = [a[:5] for a in viejo["aristas"]]
        self.assertEqual(conexiones(red=viejo), ([], []))

    def test_senal_legal_sin_fuente_no_se_publica(self):
        mal = copy.deepcopy(LEGAL)
        mal["senales"][0]["fuentes"] = []
        problemas, _ = conexiones(legal=mal)
        self.assertTrue(any("no cita ninguna fuente" in p for p in problemas))

    def test_senal_legal_sin_contratista_no_se_publica(self):
        mal = copy.deepcopy(LEGAL)
        mal["senales"][0]["contratista"] = None
        problemas, _ = conexiones(legal=mal)
        self.assertTrue(any("no esta cruzada" in p for p in problemas))

    def test_senal_sin_revisar_hace_tiempo_avisa_pero_no_detiene(self):
        vieja = copy.deepcopy(LEGAL)
        vieja["senales"][0]["verificado_el"] = "2026-09-01"
        problemas, avisos = conexiones(legal=vieja)
        self.assertEqual(problemas, [])
        self.assertEqual(len(avisos), 1)
        self.assertIn("hace 78 dias", avisos[0])

    def test_las_exclusiones_automaticas_no_piden_revision_a_mano(self):
        auto = copy.deepcopy(LEGAL)
        auto["senales"][0].update(automatica=True, verificado_el="2026-01-01")
        self.assertEqual(conexiones(legal=auto), ([], []))

    def test_senal_curada_que_dejo_de_cruzar_avisa(self):
        suelta = copy.deepcopy(LEGAL)
        suelta["curadas_sin_contratos"] = 2
        problemas, avisos = conexiones(legal=suelta)
        self.assertEqual(problemas, [])
        self.assertTrue(any("2 senal" in a for a in avisos))


if __name__ == "__main__":
    unittest.main()
