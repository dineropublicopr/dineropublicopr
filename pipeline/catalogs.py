"""Catalogos de referencia: agencias, categorias de servicio, metodos y fondos.

Los metodos de contratacion y origenes de fondos son listas cerradas que el portal
entrega en el HTML, asi que van fijas aqui. Las agencias se bajan del API porque son 416
y cambian con el tiempo.
"""

from __future__ import annotations

import json
from pathlib import Path

from ocpr_client import OcprClient

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Valores verificados contra el portal el 2026-10-20.
# Importante: 'Subasta Informal' (9) y 'Contrataciones por Emergencia' (10) existen en el
# formulario pero no devuelven ni un contrato, y aproximadamente la mitad de los contratos
# no traen este campo. Eso es un hallazgo sobre la calidad del dato, no un error nuestro.
CONTRACTING_FORMS = {
    1: "Subasta Pública",
    2: "Solicitud de Propuesta (RFP)",
    3: "Solicitud de Cotización",
    5: "Otros",
    7: "Solicitud de Calificaciones (RFQ)",
    9: "Subasta Informal",
    10: "Contrataciones por Emergencia",
}

FUNDS = {
    1: "Fondos Federales",
    2: "Fondos Estatales",
    3: "Ingresos Propios",
    4: "Fondos Combinados",
    6: "Fondos Legislativos",
}

SERVICE_GROUPS = {
    1: "COMPRA, VENTA Y ALQUILER DE EQUIPO, VEHÍCULOS Y OTROS",
    2: "COMPRA, VENTA, ALQUILER Y/O DESARROLLO DE INMUEBLES",
    3: "COMPRA DE MATERIALES, SUMINISTROS Y EFECTOS",
    4: "ADQUISICIÓN DE SEGUROS",
    5: "CONSTRUCCIÓN Y REPARACIÓN DE VÍAS PÚBLICAS",
    6: "CONSTRUCCIÓN Y REPARACIÓN DE ESTRUCTURAS",
    7: "PRÉSTAMOS Y BECAS",
    8: "RESCISIÓN DE CONTRATOS",
    9: "SERVICIOS EDUCATIVOS, DE ADIESTRAMIENTO Y DE ORIENTACIONES",
    10: "SERVICIOS DE CONSULTORÍA",
    11: "SERVICIOS DE PUBLICIDAD, DE REPRESENTACIÓN O ARTÍSTICOS",
    12: "SERVICIOS MISCELÁNEOS NO PERSONALES",
    13: "SERVICIOS PERSONALES NO PROFESIONALES",
    14: "SERVICIOS RELACIONADOS CON LA SALUD",
    15: "SERVICIOS TÉCNICOS",
    16: "TRANSFERENCIA DE FONDOS",
    17: "ACUERDOS FINANCIEROS Y NO FINANCIEROS",
    18: "COMPRA Y VENTA DE OBRAS DE ARTES Y TESOROS HISTÓRICOS",
    19: "PROYECTOS RELACIONADOS A ENERGÍA RENOVABLE Y NO RENOVABLE",
    20: "SERVICIOS PROFESIONALES",
    21: "SERVICIOS RELACIONADOS A LOS SISTEMAS DE INFORMACIÓN",
    22: "RECOGIDO DE DESPERDICIOS Y RECICLAJE",
    23: "INTERAGENCIALES",
}


def fetch_entities(client: OcprClient) -> list[dict]:
    """Las 416 entidades gubernamentales, con su rama o grupo."""
    entities: list[dict] = []
    page = 1
    while True:
        result = client.entities(page=page, page_size=100)
        batch = result.get("Results") or []
        if not batch:
            break
        entities.extend(batch)
        if len(entities) >= (result.get("Total") or 0):
            break
        page += 1
    return entities


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = OcprClient()
    client.open_session()

    entities = fetch_entities(client)
    print(f"entidades: {len(entities)}")

    groups: dict[str, int] = {}
    for entity in entities:
        groups[entity.get("GroupName") or "(sin grupo)"] = (
            groups.get(entity.get("GroupName") or "(sin grupo)", 0) + 1
        )
    for name, count in sorted(groups.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>4}  {name}")

    payload = {
        "entities": entities,
        "contracting_forms": CONTRACTING_FORMS,
        "funds": FUNDS,
        "service_groups": SERVICE_GROUPS,
    }
    target = OUT_DIR / "catalogs.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nguardado en {target}")


if __name__ == "__main__":
    main()
