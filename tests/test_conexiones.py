"""Pruebas del cruce de cabilderos: el filtro de personas, de cuando es cada conexion y el
historial de lo que Justicia deja de publicar.

No tocan la red ni los datos reales: arman una base SQLite en memoria con tres contratos.
"""

from __future__ import annotations

import sqlite3
import unittest

import _ruta  # noqa: F401

import conexiones
from build_db import normalize_name
from historial import actualizar_historial


def base() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE contracts (contract_id INTEGER PRIMARY KEY, contract_number TEXT,
            entity_name TEXT, date_of_grant TEXT, fiscal_year TEXT, service_group TEXT,
            amount_to_pay REAL, document_id TEXT);
        CREATE TABLE contractors (contract_id INTEGER, name TEXT, name_key TEXT);
        """
    )
    contratos = [
        (1, "2024-000001", "Departamento de Salud", "2023-07-15", "2023-24", "TI", 100.0, "d1"),
        (2, "2026-000002", "Departamento de Salud", "2026-10-02", "2026-27", "TI", 300.0, "d2"),
        (3, "2025-000003", "Municipio de Ponce", "2025-01-20", "2024-25", "TI", 50.0, "d3"),
    ]
    conn.executemany("INSERT INTO contracts VALUES (?,?,?,?,?,?,?,?)", contratos)
    for contract_id, nombre in ((1, "ACME TECHNOLOGIES, INC."), (2, "ACME TECHNOLOGIES INC"),
                                (3, "ACME TECHNOLOGIES, INC.")):
        conn.execute(
            "INSERT INTO contractors VALUES (?,?,?)", (contract_id, nombre, normalize_name(nombre))
        )
    return conn


CRUDO = {
    "justicia": {
        "descargado": "2026-11-15T22:49:43-04:00",
        "registros": [
            {
                "nombre": "Firma Uno LLC",
                "registro": "2026Q1-00001",
                "clientes": ["Acme Technologies, Inc", "Juana Del Pueblo (Alianza de Puerto Rico)"],
                "personal": ["Ana Perez"],
                "certificado": "https://registrodecabilderos.pr.gov/Lobbyist/Certify/uno",
            },
            {
                "nombre": "Firma Uno LLC",
                "registro": "2026Q3-00002",
                "clientes": ["Acme Technologies, Inc", "Museo del Barrio (pro-bono)"],
                "personal": ["Ana Perez"],
                "certificado": "https://registrodecabilderos.pr.gov/Lobbyist/Certify/dos",
            },
        ],
    },
    "senado": {"descargado": "2026-11-15T22:49:43-04:00", "registros": []},
}


class FiltroDePersonasTest(unittest.TestCase):
    def test_persona_con_organizacion_entre_parentesis_no_se_publica(self):
        # Antes pasaba como organizacion por el "Puerto Rico" del parentesis.
        self.assertTrue(conexiones.may_be_person("Juana Del Pueblo (Alianza de Puerto Rico)"))
        self.assertTrue(conexiones.may_be_person("Juana Del Pueblo (sin cerrar"))

    def test_organizacion_con_aclaracion_entre_parentesis_si_se_publica(self):
        for nombre in ("Museo del Barrio (pro-bono)", "New Energy LLC (NE)", "AbbVie (Puerto Rico)"):
            self.assertFalse(conexiones.may_be_person(nombre), nombre)

    def test_casos_que_ya_funcionaban(self):
        self.assertTrue(conexiones.may_be_person("Juana Del Pueblo"))
        for nombre in ("AbbVie", "Condado 4", "Acme Technologies, Inc"):
            self.assertFalse(conexiones.may_be_person(nombre), nombre)


class CabilderosTest(unittest.TestCase):
    def setUp(self):
        self.conn = base()
        self.idx = conexiones.Contractors(self.conn)
        self.payload, self.firms = conexiones.build_cabilderos(CRUDO, self.idx)
        self.firma = self.firms[0]

    def tearDown(self):
        self.conn.close()

    def test_cada_cliente_dice_que_inscripciones_lo_declaran(self):
        clientes = {c["nombre"]: c for c in self.firma["clientes"]}
        self.assertEqual(
            clientes["Acme Technologies, Inc"]["registros"], ["2026Q3-00002", "2026Q1-00001"]
        )
        self.assertEqual(clientes["Museo del Barrio (pro-bono)"]["registros"], ["2026Q3-00002"])

    def test_el_cliente_que_puede_ser_persona_se_cuenta_pero_no_se_nombra(self):
        nombres = [c["nombre"] for c in self.firma["clientes"]]
        self.assertNotIn("Juana Del Pueblo (Alianza de Puerto Rico)", nombres)
        self.assertEqual(self.firma["clientes_sin_nombre"], 1)
        self.assertEqual(self.firma["clientes_declarados"], 3)

    def test_el_contratista_trae_las_fechas_de_sus_contratos(self):
        acme = next(c for c in self.firma["clientes"] if c["contratista"])["contratista"]
        self.assertEqual((acme["desde"], acme["hasta"]), ("2023-07-15", "2026-10-02"))
        self.assertEqual(acme["contratos"], 3)

    def test_el_grafo_dice_de_cuando_es_cada_conexion(self):
        legal = {"senales": []}
        periodo = conexiones.build_periodo(self.conn, self.payload)
        red = conexiones.build_red(self.conn, self.idx, self.firms, legal, periodo)
        nodos = {n[0]: n for n in red["nodos"]}

        self.assertEqual(red["periodo"]["contratos"]["desde"], "2023-07-15")
        self.assertEqual(red["periodo"]["contratos"]["hasta"], "2026-10-02")
        self.assertEqual(red["periodo"]["contratos"]["ano_desde"], "2023-24")
        self.assertEqual(red["periodo"]["cabildeo"]["trimestre_desde"], "2026Q1")
        self.assertEqual(red["periodo"]["cabildeo"]["trimestre_hasta"], "2026Q3")

        cabildeo = [a for a in red["aristas"] if a[2] == "l"]
        self.assertEqual(len(cabildeo), 1)
        self.assertEqual(cabildeo[0][5], ["2026Q3-00002", "2026Q1-00001"])
        firma = nodos[cabildeo[0][0]]
        self.assertEqual(
            dict(firma[4]["reg"])["2026Q1-00001"],
            "https://registrodecabilderos.pr.gov/Lobbyist/Certify/uno",
        )
        contratista = nodos[cabildeo[0][1]]
        self.assertEqual(contratista[4]["d"], ["2023-07-15", "2026-10-02"])

        por_agencia = {nodos[a[1]][2]: a for a in red["aristas"] if a[2] == "k"}
        self.assertEqual(por_agencia["Departamento de Salud"][5], ["2023-07-15", "2026-10-02"])
        self.assertEqual(por_agencia["Municipio de Ponce"][5], ["2025-01-20", "2025-01-20"])
        # Toda conexion apunta a puntos que existen.
        for origen, destino, *_ in red["aristas"]:
            self.assertIn(origen, nodos)
            self.assertIn(destino, nodos)


def foto(fecha: str, firmas: list[dict]) -> dict:
    return {"fuentes": {"justicia": {"descargado": f"{fecha}T22:00:00-04:00"}}, "firmas": firmas}


def firma(nombre: str, numeros: list[str], clientes: list[tuple[str, bool]]) -> dict:
    return {
        "nombre": nombre,
        "registros": [
            {"numero": n, "trimestre": n[:6], "certificado": f"https://ejemplo.test/{n}"} for n in numeros
        ],
        "clientes": [
            {"nombre": c, "contratista": {"clave": "X"} if cruzado else None, "registros": numeros[:1]}
            for c, cruzado in clientes
        ],
    }


class HistorialTest(unittest.TestCase):
    def setUp(self):
        self.semana1 = foto("2026-11-01", [
            firma("Firma Uno LLC", ["2026Q1-00001", "2026Q3-00002"], [("Acme Inc", True), ("Beta Corp", False)]),
            firma("Firma Dos LLC", ["2025Q4-00009"], [("Gamma LLC", False)]),
        ])
        self.semana2 = foto("2026-11-08", [
            firma("Firma Uno LLC", ["2026Q3-00002"], [("Acme Inc", True)]),
        ])

    def test_primera_foto(self):
        h = actualizar_historial(None, self.semana1)
        self.assertEqual((h["desde"], h["hasta"]), ("2026-11-01", "2026-11-01"))
        self.assertEqual(h["resumen"]["inscripciones"], 3)
        self.assertEqual(h["resumen"]["inscripciones_no_vigentes"], 0)
        self.assertTrue(all(i["vigente"] for i in h["inscripciones"]))

    def test_lo_que_deja_de_aparecer_se_conserva_con_sus_fechas(self):
        h = actualizar_historial(actualizar_historial(None, self.semana1), self.semana2)
        por_numero = {i["numero"]: i for i in h["inscripciones"]}
        self.assertEqual(h["hasta"], "2026-11-08")
        self.assertTrue(por_numero["2026Q3-00002"]["vigente"])
        self.assertEqual(por_numero["2026Q3-00002"]["primera_vez"], "2026-11-01")
        self.assertEqual(por_numero["2026Q3-00002"]["ultima_vez"], "2026-11-08")
        for ida in ("2026Q1-00001", "2025Q4-00009"):
            self.assertFalse(por_numero[ida]["vigente"])
            self.assertEqual(por_numero[ida]["ultima_vez"], "2026-11-01")
        clientes = {c["cliente"]: c for c in h["clientes"]}
        self.assertTrue(clientes["Acme Inc"]["vigente"])
        self.assertFalse(clientes["Beta Corp"]["vigente"])
        self.assertFalse(clientes["Gamma LLC"]["vigente"])
        self.assertEqual(h["resumen"]["inscripciones_no_vigentes"], 2)
        self.assertEqual(h["resumen"]["clientes_no_vigentes"], 2)

    def test_una_foto_sin_firmas_o_sin_fecha_no_toca_el_historial(self):
        previo = actualizar_historial(None, self.semana1)
        self.assertIs(actualizar_historial(previo, foto("2026-11-08", [])), previo)
        sin_fecha = {"fuentes": {"justicia": {"descargado": None}}, "firmas": self.semana2["firmas"]}
        self.assertIs(actualizar_historial(previo, sin_fecha), previo)
        self.assertIsNone(actualizar_historial(None, foto("2026-11-08", [])))

    def test_el_orden_de_las_fotos_no_cambia_el_resultado(self):
        en_orden = actualizar_historial(actualizar_historial(None, self.semana1), self.semana2)
        al_reves = actualizar_historial(actualizar_historial(None, self.semana2), self.semana1)
        for clave in ("desde", "hasta", "resumen", "inscripciones", "clientes"):
            self.assertEqual(en_orden[clave], al_reves[clave], clave)
        # Repetir una foto vieja tampoco revive lo que ya no esta.
        repetida = actualizar_historial(en_orden, self.semana1)
        self.assertEqual(repetida["inscripciones"], en_orden["inscripciones"])

    def test_una_descarga_fallida_no_da_nada_por_vencido(self):
        # Si Justicia no responde, cabilderos.py conserva la copia anterior con su fecha vieja.
        previo = actualizar_historial(None, self.semana1)
        otra_vez = actualizar_historial(previo, self.semana1)
        self.assertEqual(otra_vez["hasta"], "2026-11-01")
        self.assertEqual(otra_vez["resumen"]["inscripciones_no_vigentes"], 0)

    def test_el_filtro_de_personas_vale_tambien_para_lo_guardado(self):
        vieja = foto("2026-11-01", [
            firma("Firma Uno LLC", ["2026Q1-00001"], [
                ("Juana Del Pueblo (Alianza de Puerto Rico)", False),
                ("Radiologia del Turabo", True),
                ("Acme Inc", False),
            ]),
        ])
        h = actualizar_historial(None, vieja, es_persona=conexiones.may_be_person)
        nombres = [c["cliente"] for c in h["clientes"]]
        self.assertNotIn("Juana Del Pueblo (Alianza de Puerto Rico)", nombres)
        # Cruzado con un contratista: se publica por ese cruce, igual que en cabilderos.json.
        self.assertIn("Radiologia del Turabo", nombres)
        self.assertIn("Acme Inc", nombres)


if __name__ == "__main__":
    unittest.main()
