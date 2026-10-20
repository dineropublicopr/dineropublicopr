"""Baja las enmiendas de los contratos grandes.

Por que solo los grandes: 42,345 contratos tienen enmiendas, y a una peticion cada 1.5
segundos eso son ~17 horas. Limitarlo a los de $1 millon o mas lo baja a unas 2,400
peticiones (~1 hora), que si cabe en una corrida semanal, y es donde esta el dinero.
El umbral se puede cambiar con --min.

Hay que correrlo despues de build_db.py, porque lee de la base cuales contratos tienen
enmiendas, y antes de export_api.py, que es quien usa el resultado.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from ocpr_client import OcprClient

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "out" / "contratos.sqlite"
OUT_PATH = ROOT / "data" / "raw" / "amendments.json"

DEFAULT_MIN = 1_000_000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min", type=float, default=DEFAULT_MIN)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    targets = conn.execute(
        """SELECT contract_id, contract_number, entity_id, amount_to_pay
             FROM contracts
            WHERE has_amendments = 1 AND amount_to_pay >= ?
            ORDER BY amount_to_pay DESC""",
        (args.min,),
    ).fetchall()
    conn.close()

    if args.limit:
        targets = targets[: args.limit]

    print(f"contratos con enmiendas sobre ${args.min:,.0f}: {len(targets):,}")

    # Reanudable: si ya hay resultados en disco, no se vuelven a pedir.
    done: dict[str, list] = {}
    if OUT_PATH.exists():
        done = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        print(f"ya en disco: {len(done):,}")

    client = OcprClient()
    client.open_session()

    for index, (contract_id, number, entity_id, _amount) in enumerate(targets, 1):
        key = str(contract_id)
        if key in done:
            continue
        try:
            result = client.amendments(number, entity_id)
        except Exception as exc:  # noqa: BLE001
            print(f"  fallo {number}: {type(exc).__name__}")
            continue

        # Un contrato sin enmiendas accesibles devuelve un dict de validacion, no una lista.
        if not isinstance(result, list):
            done[key] = []
        else:
            done[key] = [
                {
                    "amendment": row.get("Amendment"),
                    "amount": row.get("AmountToPay"),
                    "date": row.get("DateOfGrant"),
                    "contract_id": row.get("ContractId"),
                }
                for row in result
            ]

        if index % 50 == 0:
            OUT_PATH.write_text(
                json.dumps(done, ensure_ascii=False), encoding="utf-8"
            )
            print(f"  {index:,} / {len(targets):,}", flush=True)

    OUT_PATH.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
    total = sum(len(v) for v in done.values())
    print(f"\nlisto: {len(done):,} contratos, {total:,} enmiendas")


if __name__ == "__main__":
    main()
