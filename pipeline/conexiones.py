"""Cruza cabilderos y señales legales con el Registro de Contratos.

Genera tres archivos para el sitio:

- cabilderos.json  firmas registradas, su personal autorizado, sus clientes y cuáles de esos
                   clientes reciben contratos del gobierno.
- legal.json       empresas contratistas que aparecen en una fuente oficial como acusadas,
                   condenadas o excluidas, con sus contratos y cuáles se otorgaron después.
- red.json         el grafo que dibuja la pestaña «Conexiones»: agencias, contratistas,
                   firmas de cabildeo y señales legales, y cómo se tocan.

Tres reglas que gobiernan todo el archivo:

1. Ningún nodo, señal ni texto de la parte legal lleva el nombre de una persona natural.
   El vínculo entre una persona y una empresa solo aparece si la fuente oficial lo hace, y
   aun entonces sin el nombre (ver data/legal/README.md).

2. Un cliente de cabildeo solo se cruza con un contratista si al menos uno de los dos nombres
   es claramente de una organización (LLC, Inc., Municipio, Colegio...). Dos personas con el
   mismo nombre no son la misma persona, y un cliente individual de una firma de abogados no
   tiene por qué ser el médico homónimo que le factura a Salud.

3. Las listas federales de exclusión solo se cruzan con entidades de Puerto Rico y con nombres
   de al menos dos palabras. Probado sin esos filtros, el cruce le ponía bandera roja a
   contratistas de PR por coincidir con sanciones de OFAC llamadas «H» o «THE BASE».
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from build_db import normalize_name

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "out" / "contratos.sqlite"
CABILDEROS_RAW = ROOT / "data" / "raw" / "cabilderos.json"
EXCLUSIONES_RAW = ROOT / "data" / "raw" / "exclusiones.json"
SENALES_CURADAS = ROOT / "data" / "legal" / "senales.json"
API_DIR = ROOT / "web" / "datos"

AST = ZoneInfo("America/Puerto_Rico")

# Contratistas que entran al grafo además de los que tienen cabildeo o señal legal.
GRAPH_TOP_CONTRACTORS = 1000
# Aristas contratista-agencia por contratista. El resto se resume en un conteo.
GRAPH_AGENCIES_PER_CONTRACTOR = 15

NIVELES = {"condena": 1, "exclusion": 1, "acusacion": 2, "demanda_gobierno": 3}

# Palabras que delatan una organización. No tiene que ser exhaustiva: un falso negativo solo
# deja un cruce sin hacer, que es el error barato.
ORG_MARKERS = re.compile(
    r"\b(LLC|L L C|INC|CORP|CORPORATION|CORPORACION|PSC|P S C|CSP|C S P|LLP|LP|LTD|LIMITED|"
    r"CO|COMPANY|COMPANIA|GROUP|GRUPO|HOLDINGS?|PARTNERS|ASSOCIATES|ENTERPRISES?|EMPRESAS|"
    r"INDUSTRIES|INDUSTRIAS|SERVICES|SERVICIOS|SYSTEMS|SISTEMAS?|TECHNOLOGIES|TECHNOLOGY|"
    r"CONSULTING|CONSULTANTS|MANAGEMENT|SOLUTIONS|INTERNATIONAL|CARIBE|CARIBBEAN|"
    r"MUNICIPIO|MUNICIPALITY|ASOCIACION|ASSOCIATION|COLEGIO|FIDEICOMISO|TRUST|FUNDACION|"
    r"FOUNDATION|COOPERATIVA|HOSPITAL|UNIVERSIDAD|UNIVERSITY|INSTITUTO|INSTITUTE|BANCO|BANK|"
    r"BANCORP|AUTORIDAD|AUTHORITY|CONSORCIO|ALIANZA|ALLIANCE|NETWORK|CENTER|CENTRO|CLINICA|"
    r"LABORATORIO|LABS?|FARMACIA|PHARMACEUTICALS?|MEDICAL|HEALTH|HEALTHCARE|SALUD|ENERGY|"
    r"ENERGIA|CONSTRUCTION|CONSTRUCTORA|DEVELOPMENT|DESARROLLOS?|INSURANCE|SEGUROS|CAPITAL|"
    r"FUND|RESORT|HOTEL|SUPERMERCADOS?|MARKETPLACE|FOODS?|LINEAS|GOBIERNO|DEPARTAMENTO|"
    r"MUSEO|SOCIETY|SOCIEDAD|S E|SE|BV|B V|GMBH|SARL|SA|S A|PC|P C|PLLC|LTDA|"
    r"FIRSTBANK|PUERTO RICO|PR|USA|UPR|ORGANIZATION|ORGANIZACION|FEDERATION|FEDERACION|"
    r"HOSPITALES|HOSPICE|HOSPITALITY|COLLEGE|COUNCIL|CONSEJO|PLAZA|DEVELOPMENTS|INVESTMENTS?|"
    r"ASOCIADOS|ASOCIADAS|DESTILERIA|RECINTO|DIVISION|CHEMICALS|INNOVATIONS?|RE|CANTERA|"
    r"HIPODROMO|DISTRIBUTORS?|UNION)\b"
)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^A-Z0-9 ]+", " ", text.upper())


def is_org(name: str) -> bool:
    return bool(ORG_MARKERS.search(fold(name)))


def may_be_person(name: str) -> bool:
    """Si un nombre podría ser de una persona natural y por eso no se publica.

    Errar hacia el lado de esconder: un nombre de varias palabras sin ninguna marca de
    organización se trata como persona. Una sola palabra («AbbVie», «T-Mobile») o un número
    («Condado 4») delatan una marca o un proyecto, no a alguien.
    """
    if is_org(name) or re.search(r"\d", name):
        return False
    return len(name.split()) >= 2


def person_key(name: str) -> str:
    """Clave para no repetir a la misma persona escrita con otro espaciado o capitalización."""
    return re.sub(r"\s+", " ", fold(name)).strip()


def merge_people(names: list[str]) -> list[str]:
    """Funde las grafías de una misma persona dentro de UNA firma.

    Justicia recibe a la misma gente escrita de varias formas en cada inscripción:
    «ESTEBAN LOPEZ» y «ESTEBAN A LOPEZ ROSADO». Si todas las palabras de un nombre (sin
    iniciales sueltas) están en otro más largo de la misma firma, es la misma persona y se
    queda la versión más completa. Solo se compara dentro de una firma: entre firmas, dos
    nombres parecidos siguen siendo dos personas.
    """
    def words(name: str) -> frozenset:
        return frozenset(w for w in person_key(name).split() if len(w) > 1)

    ordered = sorted({person_key(n): n for n in names}.values(), key=lambda n: -len(words(n)))
    kept: list[str] = []
    for name in ordered:
        if not any(words(name) <= words(other) for other in kept):
            kept.append(name)
    return sorted(kept, key=lambda n: n.casefold())


def skeleton(text: str) -> str:
    """Consonantes de un nombre, sin la N.

    Los PDF del Senado pierden las letras acentuadas al nombrar el archivo: «Valdés» queda
    «Valds» y «Montañez», «Montaez». Comparar solo consonantes (y sin la N, por la Ñ)
    reconoce la firma igual.
    """
    return re.sub(r"[^B-DF-HJ-MP-TV-Z]", "", fold(text).replace("N", ""))


def write(name: str, payload) -> None:
    API_DIR.mkdir(parents=True, exist_ok=True)
    target = API_DIR / name
    target.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"  {name:<20} {target.stat().st_size / 1024:>9,.1f} KB")


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


# ── Índice de contratistas ─────────────────────────────────────────────────


class Contractors:
    """Resumen por clave normalizada de todo el Registro de Contratos."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.summary: dict[str, dict] = {}
        rows = conn.execute(
            """SELECT ct.name_key,
                      MIN(ct.name),
                      COUNT(DISTINCT ct.name),
                      COUNT(DISTINCT ct.contract_id),
                      COUNT(DISTINCT c.entity_name),
                      COALESCE(SUM(c.amount_to_pay), 0)
                 FROM contractors ct
                 JOIN contracts c ON c.contract_id = ct.contract_id
                GROUP BY ct.name_key"""
        )
        for key, name, variants, contracts, agencies, amount in rows:
            self.summary[key] = {
                "clave": key,
                "nombre": name,
                "variantes": variants,
                "contratos": contracts,
                "agencias": agencies,
                "monto": round(amount, 2),
            }
        self._names: dict[str, list[str]] = {}

    def names(self, key: str) -> list[str]:
        if key not in self._names:
            self._names[key] = [
                row[0]
                for row in self.conn.execute(
                    "SELECT DISTINCT name FROM contractors WHERE name_key = ? LIMIT 8", (key,)
                )
            ]
        return self._names[key]

    def match(self, name: str, require_org: bool = True, min_words: int = 1) -> dict | None:
        """El contratista con la misma clave, o None si el cruce no es defendible."""
        key = normalize_name(name)
        if len(key) < 4 or len(key.split()) < min_words or key.isdigit():
            return None
        found = self.summary.get(key)
        if not found:
            return None
        if require_org and not (is_org(name) or any(is_org(n) for n in self.names(key))):
            return None
        return found

    def public(self, key: str) -> dict:
        """Lo que el sitio muestra de un contratista cruzado, con sus grafías para dudar."""
        info = dict(self.summary[key])
        info["nombres"] = self.names(key)[:5]
        return info

    def contracts(self, key: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT c.contract_number, c.entity_name, c.date_of_grant, c.fiscal_year,
                      c.amount_to_pay, c.service_group, c.document_id
                 FROM contractors ct JOIN contracts c ON c.contract_id = ct.contract_id
                WHERE ct.name_key = ?
                GROUP BY c.contract_id
                ORDER BY c.date_of_grant DESC""",
            (key,),
        )
        return [
            {
                "numero": number,
                "agencia": agency,
                "fecha": date,
                "ano": fy,
                "monto": amount,
                "categoria": group,
                "doc": doc,
            }
            for number, agency, date, fy, amount, group, doc in rows
        ]


# ── Cabilderos ────────────────────────────────────────────────────────────


def quarter_of(registro: str) -> str:
    match = re.match(r"(\d{4}Q\d)", registro or "")
    return match.group(1) if match else ""


def build_cabilderos(raw: dict, idx: Contractors) -> tuple[dict, list[dict]]:
    justicia = (raw.get("justicia") or {}).get("registros") or []
    senado = (raw.get("senado") or {}).get("registros") or []

    # Una misma firma se inscribe varias veces (cada trimestre, o una vez por grupo de
    # clientes). Se agrupan por nombre normalizado y se unen clientes y personal.
    firms: dict[str, dict] = {}
    for rec in justicia:
        key = normalize_name(rec["nombre"]) or rec["nombre"]
        firm = firms.setdefault(
            key,
            {
                "id": "f" + str(len(firms) + 1),
                "nombre": rec["nombre"],
                "registros": [],
                "personal": {},
                "clientes": {},
            },
        )
        firm["registros"].append(
            {
                "numero": rec["registro"],
                "trimestre": quarter_of(rec["registro"]),
                "certificado": rec.get("certificado"),
            }
        )
        for person in rec.get("personal") or []:
            firm["personal"].setdefault(person_key(person), re.sub(r"\s+", " ", person))
        for client in rec.get("clientes") or []:
            firm["clientes"].setdefault(normalize_name(client) or client.upper(), client)

    out_firms = []
    for key, firm in firms.items():
        registros = sorted(firm["registros"], key=lambda r: r["numero"], reverse=True)
        personal = merge_people(list(firm["personal"].values()))
        # Quien se inscribe sin personal autorizado es una persona que cabildea por sí misma.
        individuo = not personal and not is_org(firm["nombre"])
        if individuo:
            personal = [firm["nombre"]]

        clients = []
        hidden = 0
        for client in firm["clientes"].values():
            matched = idx.match(client)
            # Un cliente que puede ser una persona natural no se publica por nombre: no se
            # inscribió él, lo declaró la firma. Se cuenta, y su nombre consta en el
            # certificado oficial enlazado. Ni siquiera viaja en el JSON público.
            if not matched and may_be_person(client):
                hidden += 1
                continue
            clients.append(
                {
                    "nombre": client,
                    "contratista": idx.public(matched["clave"]) if matched else None,
                }
            )
        clients.sort(key=lambda c: (-(c["contratista"] or {}).get("monto", -1), c["nombre"].casefold()))

        own = idx.match(firm["nombre"])
        out_firms.append(
            {
                "id": firm["id"],
                "nombre": firm["nombre"],
                "individuo": individuo,
                "registros": registros,
                "personal": personal,
                "clientes": clients,
                "clientes_sin_nombre": hidden,
                "clientes_declarados": len(clients) + hidden,
                "clientes_con_contratos": sum(1 for c in clients if c["contratista"]),
                "monto_clientes": round(
                    sum(c["contratista"]["monto"] for c in clients if c["contratista"]), 2
                ),
                "contratista": idx.public(own["clave"]) if own else None,
            }
        )
    out_firms.sort(key=lambda f: (-f["monto_clientes"], -f["clientes_declarados"]))

    senado_rows = build_senado(senado, firms, idx)

    unique_clients = {
        normalize_name(client) or client
        for firm in firms.values() for client in firm["clientes"].values()
    }
    matched_keys = {
        c["contratista"]["clave"] for f in out_firms for c in f["clientes"] if c["contratista"]
    }
    resumen = {
        "firmas": len(out_firms),
        "inscripciones": len(justicia),
        "personas_autorizadas": len(
            {person_key(p) for f in out_firms for p in f["personal"]}
        ),
        "clientes": len(unique_clients),
        "clientes_con_contratos": len(matched_keys),
        "monto_clientes": round(sum(idx.summary[k]["monto"] for k in matched_keys), 2),
        "firmas_contratistas": sum(1 for f in out_firms if f["contratista"]),
        "senado_inscripciones": len(senado_rows),
    }

    payload = {
        "generado": datetime.now(AST).isoformat(timespec="seconds"),
        "fuentes": {
            "justicia": {
                "nombre": "Registro de Cabilderos, Departamento de Justicia (OE-2019-031)",
                "url": "https://registrodecabilderos.pr.gov/Lobbyist",
                "descargado": (raw.get("justicia") or {}).get("descargado"),
            },
            "senado": {
                "nombre": "Registro de Cabilderos, Senado de Puerto Rico (OA 05-16)",
                "url": "https://senado.pr.gov/registro-cabilderos",
                "descargado": (raw.get("senado") or {}).get("descargado"),
            },
        },
        "resumen": resumen,
        "firmas": out_firms,
        "senado": senado_rows,
    }
    return payload, out_firms


def build_senado(senado: list[dict], justicia_firms: dict, idx: Contractors) -> list[dict]:
    """Inscripciones del Senado, con la firma deducida cuando se puede defender.

    El Senado no tiene columna de firma. Se deduce de dos formas, y se dice cuál se usó:

    - «archivo»: el PDF suele llamarse «Persona---Firma-LLC.pdf». Si el nombre de una firma
      conocida aparece en el nombre del archivo, esa es la firma.
    - «justicia»: la persona figura como personal autorizado de una firma en el registro de
      Justicia. Son dos registros de cabilderos, así que es la misma gente en el mismo oficio.

    Nunca se adivina la firma por pedazos del nombre de la persona.
    """
    known: dict[str, str] = {}
    for key, firm in justicia_firms.items():
        if is_org(firm["nombre"]) and len(key) >= 4:
            known[key] = firm["nombre"]
    for rec in senado:
        if rec.get("tipo", "").lower().startswith("corpor") and is_org(rec["nombre"]):
            known.setdefault(normalize_name(rec["nombre"]), rec["nombre"])
    # Con menos de 6 consonantes el esqueleto es demasiado ambiguo: el de «Ferraiuoli» (FRRL)
    # cabe dentro del de «O'Ferrall», y un archivo con solo el nombre de la persona acababa
    # asignándole una firma en la que no aparece.
    known_skel = {skeleton(key): name for key, name in known.items() if len(skeleton(key)) >= 6}

    # Personal autorizado de Justicia -> firma, solo si la persona está en una sola firma.
    staff: dict[tuple, set] = defaultdict(set)
    for firm in justicia_firms.values():
        if not is_org(firm["nombre"]):
            continue
        for person in firm["personal"].values():
            staff[tuple(w for w in person_key(person).split() if len(w) > 1)].add(firm["nombre"])

    rows = []
    for rec in senado:
        filename = unquote((rec.get("pdf") or "").rsplit("/", 1)[-1]).rsplit(".", 1)[0]
        firm, how = None, None
        if is_org(rec["nombre"]):
            firm, how = rec["nombre"], "registro"
        else:
            # Lo que queda del archivo después de quitarle el nombre de la persona. Si no
            # queda nada, el archivo no dice firma alguna.
            rest_key = f" {normalize_name(filename.replace('-', ' '))} ".replace(
                f" {normalize_name(rec['nombre'])} ", " "
            )
            rest_skel = skeleton(rest_key).replace(skeleton(rec["nombre"]), "", 1)
            for key in sorted(known, key=len, reverse=True):
                if f" {key} " in rest_key:
                    firm, how = known[key], "archivo"
                    break
            if not firm:
                for skel in sorted(known_skel, key=len, reverse=True):
                    if skel in rest_skel:
                        firm, how = known_skel[skel], "archivo"
                        break
        if not firm:
            mine = [w for w in person_key(rec["nombre"]).split() if len(w) > 1]
            candidates = set()
            for words, firms in staff.items():
                # Mismo primer nombre y todas las palabras contenidas: «Rafael Rojas» no es
                # «Osvaldo Rafael Rojas Vega».
                if len(mine) >= 2 and words and mine[0] == words[0] and set(mine) <= set(words):
                    candidates |= firms
            if len(candidates) == 1:
                firm, how = candidates.pop(), "justicia"
        matched = idx.match(firm) if firm else None
        rows.append(
            {
                "nombre": rec["nombre"],
                "tipo": rec.get("tipo"),
                "fecha": rec.get("fecha"),
                "pdf": rec.get("pdf"),
                "firma": firm,
                "firma_fuente": how,
                "firma_contratista": idx.public(matched["clave"]) if matched else None,
            }
        )
    return rows


# ── Señales legales ───────────────────────────────────────────────────────


def build_legal(exclusiones: dict, curadas: dict, idx: Contractors) -> dict:
    senales = []
    automaticas = []

    for source_key, label, url in (
        ("leie", "LEIE, Oficina del Inspector General de HHS", "https://oig.hhs.gov/exclusions/"),
        ("sam", "Exclusiones de SAM.gov (contratación federal)", "https://sam.gov/search/?index=ex"),
    ):
        block = exclusiones.get(source_key)
        if not block:
            automaticas.append({"fuente": label, "url": url, "activa": False})
            continue
        entities = block.get("entidades_pr") or []
        hits = 0
        for ent in entities:
            matched = idx.match(ent["nombre"], require_org=False, min_words=2)
            if not matched:
                continue
            hits += 1
            senales.append(
                {
                    "entidad": ent["nombre"],
                    "tipo": "exclusion",
                    "nivel": 1,
                    "jurisdiccion": f"Federal ({ent.get('agencia') or source_key.upper()})",
                    "fecha": ent.get("desde"),
                    "fecha_nota": "inicio de la exclusión",
                    "alegacion": ent.get("motivo"),
                    "estatus": "Exclusión vigente en la lista descargada el "
                    + (block.get("descargado") or "")[:10],
                    "vinculo": "La fuente la excluye por estar controlada por una persona sancionada."
                    if ent.get("codigo") == "1128b8"
                    else None,
                    "fuentes": [{"titulo": label, "emisor": label, "url": url, "oficial": True,
                                 "fecha": (block.get("descargado") or "")[:10]}],
                    "verificado_el": (block.get("descargado") or "")[:10],
                    "automatica": True,
                    "_clave": matched["clave"],
                }
            )
        automaticas.append(
            {
                "fuente": label,
                "url": url,
                "activa": True,
                "descargado": block.get("descargado"),
                "entidades_pr": len(entities),
                "coincidencias": hits,
            }
        )

    sin_contratos = 0
    for entry in curadas.get("senales") or []:
        # Las curadas sí son de organizaciones por definición: se exige igual el marcador,
        # porque el cruce sigue siendo por nombre.
        matched = idx.match(entry["entidad"])
        if not matched:
            sin_contratos += 1
            continue
        item = {k: v for k, v in entry.items() if not k.startswith("_")}
        item["nivel"] = NIVELES.get(entry.get("tipo"), 3)
        item["automatica"] = False
        item["_clave"] = matched["clave"]
        senales.append(item)

    for item in senales:
        key = item.pop("_clave")
        item["contratista"] = idx.public(key)
        contracts = idx.contracts(key)
        since = item.get("fecha") or ""
        for c in contracts:
            c["posterior"] = bool(since and c["fecha"] and c["fecha"] >= since[:10])
        item["contratos"] = contracts
        item["contratos_posteriores"] = sum(1 for c in contracts if c["posterior"])
        item["monto_posterior"] = round(
            sum(c["monto"] or 0 for c in contracts if c["posterior"]), 2
        )

    senales.sort(key=lambda s: (s["nivel"], -s["contratista"]["monto"]))
    return {
        "generado": datetime.now(AST).isoformat(timespec="seconds"),
        "automaticas": automaticas,
        "curadas_sin_contratos": sin_contratos,
        "senales": senales,
    }


# ── Grafo ─────────────────────────────────────────────────────────────────


def build_red(conn: sqlite3.Connection, idx: Contractors, firms: list[dict], legal: dict) -> dict:
    """Nodos y aristas para la vista de conexiones, en formato compacto.

    nodos:   [id, tipo, nombre, monto, extra]   tipo: a agencia, c contratista,
                                                f firma de cabildeo, s señal legal
    aristas: [origen, destino, tipo, monto, contratos]
             tipo: k contrato (c-a), l cabildeo (f-c), p la firma misma contrata (f-c),
                   s señal (s-c)
    """
    include: set[str] = set(
        key
        for key, _ in sorted(idx.summary.items(), key=lambda kv: -kv[1]["monto"])[
            :GRAPH_TOP_CONTRACTORS
        ]
    )
    for firm in firms:
        for client in firm["clientes"]:
            if client["contratista"]:
                include.add(client["contratista"]["clave"])
        if firm["contratista"]:
            include.add(firm["contratista"]["clave"])
    for sig in legal["senales"]:
        include.add(sig["contratista"]["clave"])

    nodes: list[list] = []
    node_id: dict[tuple[str, str], str] = {}

    def add(kind: str, key: str, name: str, amount: float = 0, extra=None) -> str:
        ident = node_id.get((kind, key))
        if ident is None:
            ident = f"{kind}{len(nodes)}"
            node_id[(kind, key)] = ident
            nodes.append([ident, kind, name, round(amount or 0), extra or {}])
        return ident

    contractor_ids = {}
    for key in include:
        info = idx.summary[key]
        contractor_ids[key] = add(
            "c", key, info["nombre"], info["monto"],
            {"n": info["contratos"], "v": info["variantes"], "k": key},
        )

    edges: list[list] = []

    # Contratista-agencia, de una sola consulta.
    per_contractor: dict[str, list] = defaultdict(list)
    keys = list(include)
    for start in range(0, len(keys), 500):
        chunk = keys[start:start + 500]
        rows = conn.execute(
            f"""SELECT ct.name_key, c.entity_name, COUNT(DISTINCT c.contract_id),
                       COALESCE(SUM(c.amount_to_pay), 0)
                  FROM contractors ct JOIN contracts c ON c.contract_id = ct.contract_id
                 WHERE ct.name_key IN ({','.join('?' * len(chunk))})
                   AND c.entity_name IS NOT NULL
                 GROUP BY ct.name_key, c.entity_name""",
            chunk,
        )
        for key, agency, count, amount in rows:
            per_contractor[key].append((agency, count, amount))

    agency_totals = dict(
        conn.execute(
            """SELECT entity_name, COALESCE(SUM(amount_to_pay), 0) FROM contracts
                WHERE entity_name IS NOT NULL GROUP BY entity_name"""
        ).fetchall()
    )
    for key, rows in per_contractor.items():
        rows.sort(key=lambda r: -r[2])
        cid = contractor_ids[key]
        for agency, count, amount in rows[:GRAPH_AGENCIES_PER_CONTRACTOR]:
            aid = add("a", agency, agency, agency_totals.get(agency, 0))
            edges.append([cid, aid, "k", round(amount), count])
        hidden = len(rows) - GRAPH_AGENCIES_PER_CONTRACTOR
        if hidden > 0:
            nodes[int(cid[1:])][4]["mas"] = hidden

    for firm in firms:
        fid = add(
            "f", firm["id"], firm["nombre"], firm["monto_clientes"],
            {"cl": firm["clientes_declarados"], "pe": len(firm["personal"]), "ind": firm["individuo"],
             "fid": firm["id"]},
        )
        for client in firm["clientes"]:
            if client["contratista"]:
                ck = client["contratista"]["clave"]
                edges.append([fid, contractor_ids[ck], "l", round(idx.summary[ck]["monto"]), 0])
        if firm["contratista"]:
            ck = firm["contratista"]["clave"]
            edges.append([fid, contractor_ids[ck], "p", round(idx.summary[ck]["monto"]), 0])
            nodes[int(contractor_ids[ck][1:])][4]["firma"] = fid

    for i, sig in enumerate(legal["senales"]):
        sid = add(
            "s", str(i), {1: "Condena o exclusión", 2: "Acusación formal", 3: "Demanda del gobierno"}[sig["nivel"]],
            0, {"nivel": sig["nivel"], "fecha": sig.get("fecha"), "post": sig["contratos_posteriores"],
                "i": i},
        )
        ck = sig["contratista"]["clave"]
        edges.append([sid, contractor_ids[ck], "s", 0, 0])
        nodes[int(contractor_ids[ck][1:])][4]["senal"] = sig["nivel"]

    # Marca de cabildeo en el contratista, para dibujarla sin recorrer aristas.
    for edge in edges:
        if edge[2] == "l":
            nodes[int(edge[1][1:])][4]["cab"] = 1

    return {
        "generado": datetime.now(AST).isoformat(timespec="seconds"),
        "nodos": nodes,
        "aristas": edges,
    }


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    idx = Contractors(conn)
    print(f"contratistas indexados: {len(idx.summary):,}")
    print("generando conexiones:")

    cabilderos, firms = build_cabilderos(load_json(CABILDEROS_RAW, {}), idx)
    write("cabilderos.json", cabilderos)

    legal = build_legal(load_json(EXCLUSIONES_RAW, {}), load_json(SENALES_CURADAS, {}), idx)
    write("legal.json", legal)

    write("red.json", build_red(conn, idx, firms, legal))

    r = cabilderos["resumen"]
    print(
        f"\ncabildeo: {r['firmas']} firmas, {r['clientes']} clientes, "
        f"{r['clientes_con_contratos']} con contratos (${r['monto_clientes']:,.0f})"
    )
    print(f"legal: {len(legal['senales'])} señales con contratos")
    for auto in legal["automaticas"]:
        if auto.get("activa"):
            print(f"  {auto['fuente']}: {auto['entidades_pr']} entidades PR, "
                  f"{auto['coincidencias']} coincidencias")
        else:
            print(f"  {auto['fuente']}: sin datos")
    conn.close()


if __name__ == "__main__":
    main()
