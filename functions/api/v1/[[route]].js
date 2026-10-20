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
 */

const MAX_LIMIT = 500;
const DEFAULT_LIMIT = 50;

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

const json = (body, status = 200, extra = {}) =>
  new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'public, max-age=3600',
      ...CORS,
      ...extra,
    },
  });

const fail = (message, status = 400) => json({ error: message }, status);

/** Columnas por las que se permite ordenar. Nunca interpolar entrada del usuario en SQL. */
const SORTABLE = {
  fecha: 'date_of_grant',
  monto: 'amount_to_pay',
  agencia: 'entity_name',
  numero: 'contract_number',
};

function intParam(value, fallback, min, max) {
  const n = Number.parseInt(value ?? '', 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

/** Fecha ISO estricta: evita que entre cualquier cosa al SQL aunque vaya parametrizada. */
const isIsoDate = (value) => /^\d{4}-\d{2}-\d{2}$/.test(value);

async function listContracts(db, params) {
  const where = [];
  const binds = [];

  const text = (key, column, exact = false) => {
    const value = params.get(key);
    if (!value) return;
    if (exact) {
      where.push(`${column} = ?`);
      binds.push(value);
    } else {
      where.push(`${column} LIKE ?`);
      binds.push('%' + value + '%');
    }
  };

  text('agencia', 'c.entity_name');
  text('categoria', 'c.service_group');
  text('numero', 'c.contract_number');
  text('ano', 'c.fiscal_year', true);

  const metodo = params.get('metodo');
  if (metodo) {
    where.push('c.contracting_form_id = ?');
    binds.push(Number.parseInt(metodo, 10));
  }

  const fondo = params.get('fondo');
  if (fondo) {
    where.push('c.fund_id = ?');
    binds.push(Number.parseInt(fondo, 10));
  }

  const min = params.get('min');
  if (min) { where.push('c.amount_to_pay >= ?'); binds.push(Number(min)); }
  const max = params.get('max');
  if (max) { where.push('c.amount_to_pay <= ?'); binds.push(Number(max)); }

  const desde = params.get('desde');
  if (desde) {
    if (!isIsoDate(desde)) return fail('desde debe ser YYYY-MM-DD');
    where.push('c.date_of_grant >= ?'); binds.push(desde);
  }
  const hasta = params.get('hasta');
  if (hasta) {
    if (!isIsoDate(hasta)) return fail('hasta debe ser YYYY-MM-DD');
    where.push('c.date_of_grant <= ?'); binds.push(hasta);
  }

  const contratista = params.get('contratista');
  if (contratista) {
    where.push(`c.contract_id IN (
      SELECT contract_id FROM contractors WHERE name LIKE ?)`);
    binds.push('%' + contratista + '%');
  }

  const sortKey = SORTABLE[params.get('orden') || 'fecha'] || SORTABLE.fecha;
  const dir = (params.get('dir') || 'desc').toLowerCase() === 'asc' ? 'ASC' : 'DESC';
  const limit = intParam(params.get('limite'), DEFAULT_LIMIT, 1, MAX_LIMIT);
  const page = intParam(params.get('pagina'), 1, 1, 100000);
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
    return fail(
      'La base de datos no esta conectada. Falta enlazar D1 con el nombre DB en el ' +
        'proyecto de Pages.',
      503
    );
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

    const detail = path.match(/^\/contratos\/(\d+)$/);
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
      const q = url.searchParams.get('q') || '';
      const limit = intParam(url.searchParams.get('limite'), 100, 1, MAX_LIMIT);
      const result = await db
        .prepare(
          `SELECT ct.name_key AS clave, MIN(ct.name) AS nombre,
                  COUNT(DISTINCT ct.contract_id) AS contratos,
                  COUNT(DISTINCT c.entity_name) AS agencias,
                  SUM(c.amount_to_pay) AS monto
             FROM contractors ct
             JOIN contracts c ON c.contract_id = ct.contract_id
            WHERE (? = '' OR ct.name LIKE ?)
            GROUP BY ct.name_key ORDER BY monto DESC LIMIT ?`
        )
        .bind(q, '%' + q + '%', limit)
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
    return fail('Error interno: ' + err.message, 500);
  }
}
