"""Genera el API estatico en JSON a partir de la base SQLite.

Todo lo que el dashboard necesita para dibujar sale de aqui pre-agregado, para que la pagina
cargue rapido sin consultar nada. Las busquedas profundas las hace el navegador contra el
archivo SQLite por separado.

Nota sobre el encuadre de las senales: ninguna de estas metricas prueba un delito. Un
contrato sin metodo registrado puede ser descuido administrativo; un contratista con muchas
agencias puede ser simplemente bueno en lo que hace. Las senales senalan *donde mirar*, no
quien es culpable, y los textos de la interfaz deben decirlo asi.
"""

from __future__ import annotations

import csv
import gzip
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "out" / "contratos.sqlite"
AMENDMENTS = ROOT / "data" / "raw" / "amendments.json"
API_DIR = ROOT / "web" / "datos"

# Umbrales tipicos de subasta en PR. Una acumulacion de contratos justo por debajo
# merece revision: puede indicar que se parte una compra para evitar subasta.
THRESHOLDS = [10_000, 25_000, 50_000, 100_000, 250_000]


def write(name: str, payload) -> None:
    API_DIR.mkdir(parents=True, exist_ok=True)
    target = API_DIR / name
    target.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"  {name:<28} {target.stat().st_size / 1024:>9,.1f} KB")


def rows(conn, sql: str, params=()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn, sql: str, params=()):
    return conn.execute(sql, params).fetchone()[0]


def build() -> None:
    conn = sqlite3.connect(DB_PATH)
    print("generando API estatico:")

    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())

    # ---- Resumen general -------------------------------------------------
    resumen = {
        "generado": meta.get("built_at"),
        "fuente": meta.get("source"),
        "contratos": one(conn, "SELECT COUNT(*) FROM contracts"),
        "monto_total": one(
            conn, "SELECT COALESCE(SUM(amount_to_pay),0) FROM contracts"
        ),
        "agencias": one(
            conn, "SELECT COUNT(DISTINCT entity_id) FROM contracts"
        ),
        "contratistas": one(
            conn, "SELECT COUNT(DISTINCT name_key) FROM contractors"
        ),
        "anos_fiscales": rows(
            conn,
            """SELECT fiscal_year AS ano,
                      COUNT(*) AS contratos,
                      COALESCE(SUM(amount_to_pay),0) AS monto
                 FROM contracts WHERE fiscal_year IS NOT NULL
                GROUP BY fiscal_year ORDER BY fiscal_year""",
        ),
        "sin_metodo": one(
            conn,
            "SELECT COUNT(*) FROM contracts WHERE contracting_form_id IS NULL",
        ),
        "sin_fondo": one(
            conn, "SELECT COUNT(*) FROM contracts WHERE fund_id IS NULL"
        ),
        # 'Otros' es el hallazgo principal: no es que falte el dato, es que el dato que hay
        # no informa nada. Dos de cada tres contratos caen ahi.
        "otros": one(
            conn,
            "SELECT COUNT(*) FROM contracts WHERE contracting_form_id = 5",
        ),
    }
    resumen["pct_otros"] = round(
        100.0 * resumen["otros"] / resumen["contratos"], 1
    ) if resumen["contratos"] else 0
    # Cobertura por ano: cambia muchisimo entre 2023-24 y los anos siguientes, y decirlo
    # evita que alguien generalice un hueco que ya se cerro.
    resumen["cobertura"] = rows(
        conn,
        """SELECT fiscal_year AS ano,
                  COUNT(*) AS contratos,
                  ROUND(100.0 * SUM(CASE WHEN contracting_form_id IS NOT NULL
                        THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_metodo,
                  ROUND(100.0 * SUM(CASE WHEN fund_id IS NOT NULL
                        THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_fondo
             FROM contracts WHERE fiscal_year IS NOT NULL
            GROUP BY fiscal_year ORDER BY fiscal_year""",
    )
    write("resumen.json", resumen)

    # ---- Metodos de contratacion y fondos --------------------------------
    write(
        "metodos.json",
        rows(
            conn,
            """SELECT COALESCE(contracting_form,'(no registrado)') AS metodo,
                      fiscal_year AS ano,
                      COUNT(*) AS contratos,
                      COALESCE(SUM(amount_to_pay),0) AS monto
                 FROM contracts WHERE fiscal_year IS NOT NULL
                GROUP BY metodo, ano ORDER BY ano, monto DESC""",
        ),
    )

    write(
        "fondos.json",
        rows(
            conn,
            """SELECT COALESCE(fund,'(no registrado)') AS fondo,
                      fiscal_year AS ano,
                      COUNT(*) AS contratos,
                      COALESCE(SUM(amount_to_pay),0) AS monto
                 FROM contracts WHERE fiscal_year IS NOT NULL
                GROUP BY fondo, ano ORDER BY ano, monto DESC""",
        ),
    )

    # ---- Agencias con indice de transparencia ----------------------------
    # El indice es simple a proposito: de los contratos que otorgo esta agencia, que
    # porcentaje dice como se adjudico. Es facil de explicar y dificil de discutir.
    write(
        "agencias.json",
        rows(
            conn,
            """SELECT c.entity_id AS id,
                      c.entity_name AS agencia,
                      e.group_name AS rama,
                      COUNT(*) AS contratos,
                      COALESCE(SUM(c.amount_to_pay),0) AS monto,
                      SUM(CASE WHEN c.contracting_form_id IS NOT NULL THEN 1 ELSE 0 END)
                          AS con_metodo,
                      SUM(CASE WHEN c.fund_id IS NOT NULL THEN 1 ELSE 0 END)
                          AS con_fondo,
                      ROUND(100.0 * SUM(
                          CASE WHEN c.contracting_form_id IS NOT NULL THEN 1 ELSE 0 END
                      ) / COUNT(*), 1) AS pct_transparencia
                 FROM contracts c
                 LEFT JOIN entities e ON e.name = c.entity_name
                WHERE c.entity_name IS NOT NULL
                GROUP BY c.entity_id, c.entity_name, e.group_name
               HAVING COUNT(*) >= 10
                ORDER BY monto DESC""",
        ),
    )

    # ---- Categorias de servicio (topicos) --------------------------------
    write(
        "categorias.json",
        rows(
            conn,
            """SELECT COALESCE(service_group,'(sin categoria)') AS categoria,
                      COUNT(*) AS contratos,
                      COALESCE(SUM(amount_to_pay),0) AS monto,
                      COUNT(DISTINCT entity_name) AS agencias
                 FROM contracts
                GROUP BY categoria ORDER BY monto DESC""",
        ),
    )

    # ---- Contratistas principales ----------------------------------------
    # `variantes` y `nombres` existen por honestidad y por proteccion legal: la
    # agrupacion por nombre normalizado es una heuristica y puede unir a dos personas
    # distintas con el mismo nombre. Mostrar cuantas grafias distintas hay detras de cada
    # fila, y cuales son, permite que el lector juzgue si la agrupacion tiene sentido en
    # vez de confiar a ciegas. Nunca se afirma que una fila sea una sola persona.
    write(
        "contratistas.json",
        rows(
            conn,
            """SELECT ct.name_key AS clave,
                      MIN(ct.name) AS nombre,
                      COUNT(DISTINCT ct.name) AS variantes,
                      (SELECT group_concat(n, ' · ') FROM (
                         SELECT DISTINCT name AS n FROM contractors
                          WHERE name_key = ct.name_key LIMIT 5)) AS nombres,
                      COUNT(DISTINCT ct.contract_id) AS contratos,
                      COUNT(DISTINCT c.entity_name) AS agencias,
                      COALESCE(SUM(c.amount_to_pay),0) AS monto
                 FROM contractors ct
                 JOIN contracts c ON c.contract_id = ct.contract_id
                GROUP BY ct.name_key
                ORDER BY monto DESC LIMIT 1000""",
        ),
    )

    # ---- Indice de busqueda ----------------------------------------------
    # El sitio es estatico, asi que no puede consultar 268,000 contratos en vivo. Lo que
    # si cabe es un indice de los contratos grandes, que es donde esta el dinero y lo que
    # la gente busca. Para el resto, el sitio enlaza al portal oficial.
    search_rows = rows(
        conn,
        """SELECT c.contract_id AS id,
                  c.contract_number AS numero,
                  c.entity_name AS agencia,
                  c.date_of_grant AS fecha,
                  c.fiscal_year AS ano,
                  c.service_group AS categoria,
                  c.amount_to_pay AS monto,
                  c.contracting_form AS metodo,
                  c.fund AS fondo,
                  c.document_id AS doc,
                  (SELECT group_concat(name, ' | ') FROM contractors
                    WHERE contract_id = c.contract_id) AS contratistas
             FROM contracts c
            WHERE c.amount_to_pay >= 250000
            ORDER BY c.amount_to_pay DESC""",
    )
    write("busqueda.json", search_rows)

    # ---- Senal: gasto concentrado al cierre del ano fiscal ---------------
    # Junio es el ultimo mes del ano fiscal de PR. Una agencia que otorga una porcion
    # desproporcionada de su dinero en junio puede estar gastando para no devolver
    # presupuesto. Merece revision, no es prueba de nada.
    write(
        "cierre_fiscal.json",
        rows(
            conn,
            """SELECT entity_name AS agencia,
                      fiscal_year AS ano,
                      COALESCE(SUM(amount_to_pay),0) AS monto_ano,
                      COALESCE(SUM(CASE WHEN substr(grant_month,6,2)='06'
                                        THEN amount_to_pay ELSE 0 END),0) AS monto_junio,
                      ROUND(100.0 * COALESCE(SUM(CASE WHEN substr(grant_month,6,2)='06'
                                        THEN amount_to_pay ELSE 0 END),0)
                            / NULLIF(SUM(amount_to_pay),0), 1) AS pct_junio
                 FROM contracts
                WHERE fiscal_year IS NOT NULL AND entity_name IS NOT NULL
                GROUP BY entity_name, fiscal_year
               HAVING SUM(amount_to_pay) > 1000000 AND pct_junio >= 25
                ORDER BY pct_junio DESC LIMIT 300""",
        ),
    )

    # ---- Senal: contratos agrupados justo bajo un umbral -----------------
    bands = []
    for threshold in THRESHOLDS:
        low = one(
            conn,
            """SELECT COUNT(*) FROM contracts
                WHERE amount_to_pay >= ? AND amount_to_pay < ?""",
            (threshold * 0.9, threshold),
        )
        high = one(
            conn,
            """SELECT COUNT(*) FROM contracts
                WHERE amount_to_pay >= ? AND amount_to_pay < ?""",
            (threshold, threshold * 1.1),
        )
        bands.append(
            {
                "umbral": threshold,
                "justo_debajo": low,
                "justo_encima": high,
                "razon": round(low / high, 2) if high else None,
            }
        )
    write("umbrales.json", bands)

    # ---- Senal: enmiendas que inflan el monto ----------------------------
    # Las enmiendas se piden aparte (amendments.py) porque el buscador no las devuelve.
    # Un contrato que crece mucho despues de adjudicado crecio justo cuando ya no habia
    # competencia, asi que el porcentaje de aumento es la cifra que importa.
    enmiendas = []
    if AMENDMENTS.exists():
        fetched = json.loads(AMENDMENTS.read_text(encoding="utf-8"))
        originals = {
            str(row["contract_id"]): row
            for row in rows(
                conn,
                """SELECT contract_id, contract_number, entity_name, fiscal_year,
                          amount_to_pay
                     FROM contracts WHERE has_amendments = 1""",
            )
        }
        for contract_id, items in fetched.items():
            base = originals.get(contract_id)
            if not base or not items:
                continue
            original = base["amount_to_pay"] or 0
            added = sum(item.get("amount") or 0 for item in items)
            if original <= 0 or added <= 0:
                continue
            enmiendas.append(
                {
                    "numero": base["contract_number"],
                    "agencia": base["entity_name"],
                    "ano": base["fiscal_year"],
                    "enmiendas": len(items),
                    "monto_original": original,
                    "monto_enmiendas": added,
                    "pct_aumento": round(100.0 * added / original, 1),
                }
            )
        enmiendas.sort(key=lambda row: -row["pct_aumento"])
        enmiendas = enmiendas[:300]
    write("enmiendas.json", enmiendas)

    # ---- Serie mensual ---------------------------------------------------
    write(
        "mensual.json",
        rows(
            conn,
            """SELECT grant_month AS mes,
                      COUNT(*) AS contratos,
                      COALESCE(SUM(amount_to_pay),0) AS monto
                 FROM contracts WHERE grant_month IS NOT NULL
                GROUP BY grant_month ORDER BY grant_month""",
        ),
    )

    # ---- Descargas completas --------------------------------------------
    # La base SQLite pesa ~100 MB, que es justo el limite de archivo de GitHub, y
    # versionar un binario nuevo cada semana inflaria el repositorio sin control. Asi que
    # lo que se publica es CSV comprimido (comprime ~10x y sirve para cualquier
    # herramienta), y la base queda local o como adjunto de un release.
    downloads = API_DIR.parent / "descargas"
    downloads.mkdir(parents=True, exist_ok=True)
    print("\ndescargas:")

    contract_sql = """
        SELECT c.contract_id, c.contract_number, c.entity_name, c.date_of_grant,
               c.fiscal_year, c.service, c.service_group, c.amount_to_pay,
               c.contracting_form, c.fund, c.has_amendments,
               (SELECT group_concat(name, ' | ') FROM contractors
                 WHERE contract_id = c.contract_id) AS contratistas
          FROM contracts c
         WHERE c.fiscal_year = ?
         ORDER BY c.date_of_grant
    """
    years = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT fiscal_year FROM contracts "
            "WHERE fiscal_year IS NOT NULL ORDER BY fiscal_year"
        )
    ]
    for year in years:
        cursor = conn.execute(contract_sql, (year,))
        header = [d[0] for d in cursor.description]
        target = downloads / f"contratos-{year}.csv.gz"
        with gzip.open(target, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(cursor)
        print(f"  {target.name:<28} {target.stat().st_size / 1048576:>9,.1f} MB")

    conn.close()
    print("\nlisto")


if __name__ == "__main__":
    build()
