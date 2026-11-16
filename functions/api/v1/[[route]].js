/**
 * API publico de DineroPúblicoPR, como Cloudflare Pages Function sobre D1.
 *
 * Vive en el mismo dominio que el sitio, asi que las rutas quedan en
 * dineropublicopr.com/api/v1/... sin subdominios ni CORS entre origenes propios.
 *
 * El dashboard no depende de esto: se dibuja con los JSON estaticos. Este API existe para
 * quien quiera hacer consultas libres (periodistas, investigadores, otras apps).
 *
 * Rutas:
 *   GET /api/v1/contratos?agencia=&contratista=&ano=&metodo=&fondo=&categoria=
 *                         &min=&max=&desde=&hasta=&orden=&limite=&pagina=
 *   GET /api/v1/contratos/:id
 *   GET /api/v1/agencias
 *   GET /api/v1/contratistas?q=
 *   GET /api/v1/resumen
 *   GET /api/v1/catalogos
 *
 * Un parametro invalido devuelve 400 con un mensaje que dice cual y por que. Los errores
 * no se cachean y nunca incluyen texto interno de la base.
 */

const MAX_LIMIT = 500;
const DEFAULT_LIMIT = 50;

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

/**
 * Solo las respuestas buenas se guardan una hora. Un error nunca se cachea: antes un 500
 * durante la recarga semanal de la base se quedaba una hora en el navegador de quien
 * consultara en ese momento.
 */
const json = (body, status = 200, extra = {}) =>
  new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': status >= 200 && status < 300 ? 'public, max-age=3600' : 'no-store',
      ...CORS,
      ...extra,
    },
  });

const fail = (message, status = 400) => json({ error: message }, status);

/**
 * Parametro invalido. Se lanza desde los validadores y onRequest lo convierte en un 400.
 * Su mensaje lo escribimos nosotros y es lo unico que se le devuelve al cliente; el texto
 * de cualquier otro error (D1, JavaScript) se registra en el log y nunca sale en la
 * respuesta.
 */
class BadRequest extends Error {}

/** Columnas por las que se permite ordenar. Nunca interpolar entrada del usuario en SQL. */
const SORTABLE = {
  fecha: 'date_of_grant',
  monto: 'amount_to_pay',
  agencia: 'entity_name',
  numero: 'contract_number',
};

/** Largo maximo de un filtro de texto. El nombre de agencia mas largo ronda los 100. */
const MAX_TEXT = 120;

/** Filtro de texto opcional. Devuelve null si no vino. */
function textParam(params, key) {
  const value = (params.get(key) ?? '').trim();
  if (!value) return null;
  if (value.length > MAX_TEXT) {
    throw new BadRequest(`${key} admite hasta ${MAX_TEXT} caracteres`);
  }
  return value;
}

/**
 * Entero opcional. Un valor que no es un entero da 400 en vez de ignorarse: antes
 * `metodo=abc` llegaba a la base como NaN y devolvia "0 resultados" con codigo 200, que
 * se lee como "no hay contratos". Fuera de rango tambien es 400, salvo con `clamp`, que
 * lo ajusta al limite (para `limite`, cuyo maximo esta documentado).
 */
function intParam(params, key, { fallback = null, min = 0, max = 2147483647, clamp = false } = {}) {
  const raw = (params.get(key) ?? '').trim();
  if (!raw) return fallback;
  if (!/^\d{1,10}$/.test(raw)) throw new BadRequest(`${key} debe ser un número entero`);
  const n = Number.parseInt(raw, 10);
  if (n < min || n > max) {
    if (clamp) return Math.min(max, Math.max(min, n));
    throw new BadRequest(`${key} debe estar entre ${min} y ${max}`);
  }
  return n;
}

/** Monto opcional: solo digitos con decimales opcionales. Nada de NaN, Infinity ni 1e9. */
function amountParam(params, key) {
  const raw = (params.get(key) ?? '').trim();
  if (!raw) return null;
  if (!/^-?\d{1,15}(\.\d{1,4})?$/.test(raw)) {
    throw new BadRequest(`${key} debe ser un número, por ejemplo 250000 o 250000.50`);
  }
  return Number(raw);
}

/** Fecha ISO estricta y real (2026-02-31 no pasa). */
function dateParam(params, key) {
  const raw = (params.get(key) ?? '').trim();
  if (!raw) return null;
  const ok = /^\d{4}-\d{2}-\d{2}$/.test(raw)
    && new Date(raw + 'T00:00:00Z').toISOString().slice(0, 10) === raw;
  if (!ok) throw new BadRequest(`${key} debe ser una fecha YYYY-MM-DD`);
  return raw;
}

/** Valor opcional de una lista cerrada. */
function choiceParam(params, key, choices, fallback) {
  const raw = (params.get(key) ?? '').trim().toLowerCase();
  if (!raw) return fallback;
  if (!choices.includes(raw)) {
    throw new BadRequest(`${key} debe ser uno de: ${choices.join(', ')}`);
  }
  return raw;
}

/**
 * "La columna contiene este texto", sin distinguir mayusculas.
 *
 * Se usa instr() y no LIKE porque D1 limita los patrones de LIKE a 50 bytes: con los dos
 * `%` que se le anadian, cualquier filtro de 49 caracteres o mas devolvia 500, y el nombre
 * completo de la agencia con mas contratos ya pasaba de ahi. instr() no tiene patron, asi
 * que tampoco trata `%` ni `_` como comodines: se busca el texto tal cual.
 */
const contains = (column) => `instr(lower(${column}), lower(?)) > 0`;

async function listContracts(db, params) {
  const where = [];
  const binds = [];

  // Toda la validacion va antes de tocar la base: un parametro malo no cuesta consultas.
  const filters = [
    ['agencia', 'c.entity_name'],
    ['categoria', 'c.service_group'],
    ['numero', 'c.contract_number'],
  ];
  for (const [key, column] of filters) {
    const value = textParam(params, key);
    if (value !== null) { where.push(contains(column)); binds.push(value); }
  }

  const ano = (params.get('ano') ?? '').trim();
  if (ano) {
    if (!/^\d{4}-\d{2}$/.test(ano)) throw new BadRequest('ano debe tener la forma 2025-26');
    where.push('c.fiscal_year = ?'); binds.push(ano);
  }

  const metodo = intParam(params, 'metodo', { min: 0, max: 9999 });
  if (metodo !== null) { where.push('c.contracting_form_id = ?'); binds.push(metodo); }

  const fondo = intParam(params, 'fondo', { min: 0, max: 9999 });
  if (fondo !== null) { where.push('c.fund_id = ?'); binds.push(fondo); }

  const min = amountParam(params, 'min');
  const max = amountParam(params, 'max');
  if (min !== null && max !== null && min > max) {
    throw new BadRequest('min no puede ser mayor que max');
  }
  if (min !== null) { where.push('c.amount_to_pay >= ?'); binds.push(min); }
  if (max !== null) { where.push('c.amount_to_pay <= ?'); binds.push(max); }

  const desde = dateParam(params, 'desde');
  const hasta = dateParam(params, 'hasta');
  if (desde !== null && hasta !== null && desde > hasta) {
    throw new BadRequest('desde no puede ser posterior a hasta');
  }
  if (desde !== null) { where.push('c.date_of_grant >= ?'); binds.push(desde); }
  if (hasta !== null) { where.push('c.date_of_grant <= ?'); binds.push(hasta); }

  const contratista = textParam(params, 'contratista');
  if (contratista !== null) {
    where.push(`c.contract_id IN (
      SELECT contract_id FROM contractors WHERE ${contains('name')})`);
    binds.push(contratista);
  }

  const sortKey = SORTABLE[choiceParam(params, 'orden', Object.keys(SORTABLE), 'fecha')];
  const dir = choiceParam(params, 'dir', ['asc', 'desc'], 'desc') === 'asc' ? 'ASC' : 'DESC';
  const limit = intParam(params, 'limite', { fallback: DEFAULT_LIMIT, min: 1, max: MAX_LIMIT, clamp: true });
  const page = intParam(params, 'pagina', { fallback: 1, min: 1, max: 100000 });
  const offset = (page - 1) * limit;

  const clause = where.length ? 'WHERE ' + where.join(' AND ') : '';

  const counted = await db
    .prepare(`SELECT COUNT(*) AS n FROM contracts c ${clause}`)
    .bind(...binds)
    .first();

  const result = await db
    .prepare(
      `SELECT c.contract_id, c.contract_number, c.entity_name, c.date_of_grant,
              c.fiscal_year, c.service, c.service_group, c.amount_to_pay,
              c.contracting_form, c.fund, c.is_amendment, c.has_amendments,
              (SELECT group_concat(name, ' | ') FROM contractors
                WHERE contract_id = c.contract_id) AS contratistas
         FROM contracts c ${clause}
        ORDER BY ${sortKey} ${dir}
        LIMIT ? OFFSET ?`
    )
    .bind(...binds, limit, offset)
    .all();

  return json({
    total: counted?.n ?? 0,
    pagina: page,
    limite: limit,
    resultados: result.results ?? [],
  });
}

export async function onRequest(context) {
  const { request, env } = context;

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS });
  }
  if (request.method !== 'GET') return fail('Solo GET', 405);

  const url = new URL(request.url);
  // El archivo esta en functions/api/v1/, asi que ese prefijo se quita para quedarnos
  // con la ruta logica (/contratos, /agencias, ...).
  const path = url.pathname.replace(/^\/api\/v1/, '').replace(/\/+$/, '');
  const db = env.DB;

  if (!db) {
    // El detalle es para quien opera el sitio y se queda en el log, no en la respuesta.
    console.error('API: falta el binding de D1 llamado DB en el proyecto de Pages');
    return fail('El API no está disponible en este momento. Intenta más tarde.', 503);
  }

  try {
    if (path === '') {
      return json({
        nombre: 'API de DineroPúblicoPR',
        fuente: 'Registro de Contratos, Oficina de la Contralora de Puerto Rico',
        aviso:
          'Datos publicos reorganizados. La exactitud de cada registro es ' +
          'responsabilidad de la agencia que lo entro. Este API no emite acusaciones.',
        rutas: [
          '/api/v1/contratos',
          '/api/v1/contratos/:id',
          '/api/v1/agencias',
          '/api/v1/contratistas?q=',
          '/api/v1/resumen',
          '/api/v1/catalogos',
        ],
      });
    }

    if (path === '/contratos') return await listContracts(db, url.searchParams);

    const detail = path.match(/^\/contratos\/(\d{1,15})$/);
    if (detail) {
      const row = await db
        .prepare('SELECT * FROM contracts WHERE contract_id = ?')
        .bind(Number(detail[1]))
        .first();
      if (!row) return fail('No encontrado', 404);
      const people = await db
        .prepare('SELECT name FROM contractors WHERE contract_id = ?')
        .bind(Number(detail[1]))
        .all();
      return json({ ...row, contratistas: (people.results ?? []).map((r) => r.name) });
    }

    if (path === '/agencias') {
      const result = await db
        .prepare(
          `SELECT entity_name AS agencia, COUNT(*) AS contratos,
                  SUM(amount_to_pay) AS monto,
                  ROUND(100.0 * SUM(CASE WHEN contracting_form_id IS NOT NULL
                        THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_transparencia
             FROM contracts WHERE entity_name IS NOT NULL
            GROUP BY entity_name ORDER BY monto DESC`
        )
        .all();
      return json(result.results ?? []);
    }

    if (path === '/contratistas') {
      const q = textParam(url.searchParams, 'q') ?? '';
      const limit = intParam(url.searchParams, 'limite', { fallback: 100, min: 1, max: MAX_LIMIT, clamp: true });
      const result = await db
        .prepare(
          `SELECT ct.name_key AS clave, MIN(ct.name) AS nombre,
                  COUNT(DISTINCT ct.contract_id) AS contratos,
                  COUNT(DISTINCT c.entity_name) AS agencias,
                  SUM(c.amount_to_pay) AS monto
             FROM contractors ct
             JOIN contracts c ON c.contract_id = ct.contract_id
            WHERE (? = '' OR ${contains('ct.name')})
            GROUP BY ct.name_key ORDER BY monto DESC LIMIT ?`
        )
        .bind(q, q, limit)
        .all();
      return json(result.results ?? []);
    }

    if (path === '/resumen') {
      const row = await db
        .prepare(
          `SELECT COUNT(*) AS contratos, SUM(amount_to_pay) AS monto_total,
                  COUNT(DISTINCT entity_name) AS agencias,
                  SUM(CASE WHEN contracting_form_id IS NULL THEN 1 ELSE 0 END)
                    AS sin_metodo
             FROM contracts`
        )
        .first();
      const years = await db
        .prepare(
          `SELECT fiscal_year AS ano, COUNT(*) AS contratos,
                  SUM(amount_to_pay) AS monto
             FROM contracts WHERE fiscal_year IS NOT NULL
            GROUP BY fiscal_year ORDER BY fiscal_year`
        )
        .all();
      return json({ ...row, anos_fiscales: years.results ?? [] });
    }

    if (path === '/catalogos') {
      return json({
        metodos: {
          1: 'Subasta Pública',
          2: 'Solicitud de Propuesta (RFP)',
          3: 'Solicitud de Cotización',
          5: 'Otros',
          7: 'Solicitud de Calificaciones (RFQ)',
          9: 'Subasta Informal',
          10: 'Contrataciones por Emergencia',
        },
        fondos: {
          1: 'Fondos Federales',
          2: 'Fondos Estatales',
          3: 'Ingresos Propios',
          4: 'Fondos Combinados',
          6: 'Fondos Legislativos',
        },
        nota:
          'Dos de cada tres contratos se clasifican como "Otros" (codigo 5), una ' +
          'categoria que no informa si hubo competencia. La cobertura del campo cambia ' +
          'mucho por ano: 49.6% en 2023-24 y sobre 99% desde 2024-25, asi que no se debe ' +
          'generalizar el hueco de 2023-24. Cerca de un 20% de los contratos no trae ' +
          'origen de fondos.',
      });
    }

    return fail('Ruta no encontrada', 404);
  } catch (err) {
    if (err instanceof BadRequest) return fail(err.message, 400);
    // Nunca se devuelve el texto del error: puede traer detalles de la base o del codigo.
    // Queda en el log de la funcion, que solo ve quien opera el sitio.
    console.error('API: error interno en', path || '/', err);
    return fail('Error interno. Intenta de nuevo más tarde.', 500);
  }
}
