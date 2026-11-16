// Ejecuta el manejador real del API (functions/api/v1/[[route]].js) fuera de Cloudflare y
// escribe en la salida estandar, como JSON, lo que respondio a cada peticion de prueba.
// Lo lanza tests/test_api.py; no se usa en produccion ni se publica (esta fuera de web/).
//
//   node tests/api_harness.mjs "functions/api/v1/[[route]].js" < casos.json
//
// La base D1 se sustituye de dos formas:
//   - "stub": un objeto que cuenta las consultas y devuelve vacio. Basta para probar la
//     validacion, que ocurre antes de tocar la base.
//   - "sqlite": una base SQLite en memoria (node:sqlite, Node 22.5 o mas nuevo) con unas
//     pocas filas inventadas, para probar que el SQL corre. Si esta version de Node no
//     trae node:sqlite, esos casos se devuelven con `omitido: true`.
//   - "roto": una base que falla con un mensaje interno, para probar que no se filtra.

import { copyFileSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const routePath = process.argv[2];
const cases = JSON.parse(readFileSync(0, 'utf8'));

// Se importa una copia con extension .mjs: el repositorio no tiene package.json y Node
// trataria el .js original como CommonJS.
const dir = mkdtempSync(join(tmpdir(), 'dpr-api-'));
const copy = join(dir, 'route.mjs');
copyFileSync(routePath, copy);
const { onRequest } = await import(pathToFileURL(copy).href);

function stubDb(counter) {
  const stmt = {
    bind() { return stmt; },
    async first() { counter.n += 1; return { n: 0 }; },
    async all() { counter.n += 1; return { results: [] }; },
  };
  return { prepare() { return stmt; } };
}

function brokenDb() {
  const boom = () => { throw new Error('D1_ERROR: tabla_secreta no such table: SQLITE_ERROR'); };
  const stmt = { bind() { return stmt; }, first: async () => boom(), all: async () => boom() };
  return { prepare() { return stmt; } };
}

let sqlite = null;
try { sqlite = await import('node:sqlite'); } catch (_) { /* Node sin node:sqlite */ }

const LONG_AGENCY = 'Autoridad de Acueductos y Alcantarillados de Puerto Rico';

function sqliteDb() {
  const db = new sqlite.DatabaseSync(':memory:');
  db.exec(`
    CREATE TABLE contracts (
      contract_id INTEGER PRIMARY KEY, contract_number TEXT NOT NULL, amendment TEXT,
      is_amendment INTEGER NOT NULL, entity_id INTEGER, entity_name TEXT,
      date_of_grant TEXT, fiscal_year TEXT, grant_month TEXT, effective_from TEXT,
      effective_to TEXT, service TEXT, service_group TEXT, amount_to_pay REAL,
      amount_to_receive REAL, cancellation_date TEXT, has_amendments INTEGER,
      document_id TEXT, contracting_form_id INTEGER, contracting_form TEXT,
      fund_id INTEGER, fund TEXT);
    CREATE TABLE contractors (contract_id INTEGER NOT NULL, name TEXT NOT NULL, name_key TEXT NOT NULL);
  `);
  const ins = db.prepare(`INSERT INTO contracts
    (contract_id, contract_number, is_amendment, entity_name, date_of_grant, fiscal_year,
     service_group, amount_to_pay, contracting_form_id, contracting_form, fund_id, fund)
    VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
  ins.run(1, '2026-000001', LONG_AGENCY, '2025-08-01', '2025-26', 'CONSTRUCCION', 1500000, 1, 'Subasta', 2, 'Estatales');
  ins.run(2, '2026-000002', LONG_AGENCY, '2025-09-15', '2025-26', 'SERVICIOS', 80000, 5, 'Otros', 1, 'Federales');
  ins.run(3, '2026-000003', 'Departamento de Prueba 100%', '2025-10-20', '2025-26', 'SERVICIOS', 300000, null, null, null, null);
  const insC = db.prepare('INSERT INTO contractors VALUES (?, ?, ?)');
  insC.run(1, 'Empresa Ficticia de Construccion y Servicios Generales del Caribe LLC', 'EMPRESA FICTICIA');
  insC.run(2, 'Otra Empresa Inventada Inc.', 'OTRA EMPRESA');
  insC.run(3, 'Otra Empresa Inventada Inc.', 'OTRA EMPRESA');

  // La misma interfaz que usa el manejador: prepare().bind().first() / .all()
  return {
    prepare(sql) {
      let args = [];
      const stmt = {
        bind(...values) { args = values; return stmt; },
        async first() { return db.prepare(sql).get(...args) ?? null; },
        async all() { return { results: db.prepare(sql).all(...args) }; },
      };
      return stmt;
    },
  };
}

// El manejador registra los errores internos con console.error; aqui se capturan para
// comprobar que el detalle va al log y no a la respuesta, y para no ensuciar la salida.
const logged = [];
console.error = (...parts) => { logged.push(parts.map(String).join(' ')); };

const out = [];
for (const c of cases) {
  const counter = { n: 0 };
  let db;
  if (c.db === 'ninguna') db = undefined;
  else if (c.db === 'roto') db = brokenDb();
  else if (c.db === 'sqlite') {
    if (!sqlite) { out.push({ nombre: c.nombre, omitido: true }); continue; }
    db = sqliteDb();
  } else db = stubDb(counter);

  logged.length = 0;
  const request = new Request('https://ejemplo.invalid' + c.ruta, { method: c.metodo || 'GET' });
  const response = await onRequest({ request, env: { DB: db } });
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (_) { body = text; }
  out.push({
    nombre: c.nombre,
    estado: response.status,
    cache: response.headers.get('Cache-Control'),
    cuerpo: body,
    texto: text,
    consultas: counter.n,
    log: logged.join('\n'),
  });
}

rmSync(dir, { recursive: true, force: true });
process.stdout.write(JSON.stringify({ sqlite: Boolean(sqlite), agenciaLarga: LONG_AGENCY, resultados: out }));
