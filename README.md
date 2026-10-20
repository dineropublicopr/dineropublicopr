# DineroPúblicoPR

Herramienta ciudadana para explorar los contratos del gobierno de Puerto Rico: quién recibe
el dinero, qué agencia lo otorgó, por cuál método y de qué fondos.

Proyecto independiente. No afiliado a ninguna agencia de gobierno, partido político ni empresa.

---

## Qué problema resuelve

El Registro de Contratos de la Oficina de la Contralora es público, pero solo se puede
consultar contrato por contrato. No se puede preguntar *«¿qué agencias no registran cómo
adjudican?»* ni *«¿qué contratista aparece en más agencias?»*. Esta herramienta baja los
datos, los organiza y los pone en forma de preguntas que un ciudadano puede hacer.

### Lo que ya existía, y por qué esto es distinto

| Proyecto | Qué hace | Hueco que deja |
|---|---|---|
| [Contratos En Ley](https://contratosenley.org) | Busca por nombre y cruza con donantes políticos y cabilderos | Sin filtro por agencia, fecha ni categoría; sin API ni descargas |
| [LicitaPR](https://licitapr.com) | Inteligencia de licitaciones | Comercial, orientado a empresas que quieren *ganar* contratos |
| [Observatorio Fiscal](https://observatoriofiscalpr.com) | Presupuesto y deuda | Nivel presupuesto, no nivel contrato |
| **ContratosPR** (Code4PuertoRico) | Lo mismo que esto, 2018–2025 | **Archivado.** Ver abajo |

### La lección del proyecto que murió

[Code4PuertoRico](https://github.com/Code4PuertoRico/contratospr-api) mantuvo
`contratospr.com` durante siete años, con 40 estrellas y 12 colaboradores. Lo archivaron en
2025. El aviso de su repositorio dice:

> *«Contratos PR no está en función y este repositorio ha sido archivado. Todavía se puede
> utilizar pero todas las dependencias no se han mantenido al día.»*

**No lo mató el gobierno ni un pleito. Lo mató el mantenimiento.** Era una aplicación Python
completa, con servidor, base de datos y un árbol de dependencias que se pudrió cuando el
equipo se quedó sin tiempo.

Este proyecto está construido como respuesta directa a eso:

- **Cero dependencias de ejecución.** El pipeline usa solo la biblioteca estándar de Python;
  el sitio es HTML sin frameworks ni librerías.
- **Nada vivo que mantener.** No hay servidor ni base de datos corriendo. El sitio son
  archivos estáticos.
- **Degradación suave.** Si la automatización semanal falla, el sitio sigue sirviendo los
  últimos datos buenos en vez de caerse.
- **Un solo punto de ruptura conocido.** Si el portal de la Contralora cambia de estructura,
  solo hay que ajustar `pipeline/ocpr_client.py`.

Si este proyecto se abandona tres años, sigue funcionando. Esa es la diferencia.

DineroPúblicoPR se enfoca en lo que falta: **agregación por agencia, método y categoría, más
señales que indican dónde mirar**, con API abierto y datos descargables.

---

## El hallazgo principal

**Dos de cada tres contratos del gobierno se clasifican como «Otros»**, una categoría que no
dice nada sobre si hubo competencia. De 268,505 contratos entre 2023-24 y 2026-27:

| Método | Contratos | Monto |
|---|---:|---:|
| **Otros** | **176,544** | $30,645,182,284 |
| No registrado | 41,308 | $6,162,200,406 |
| Solicitud de Propuesta (RFP) | 23,075 | $46,519,974,323 |
| Subasta Pública | 17,803 | $11,742,715,229 |
| Solicitud de Cotización | 8,555 | $834,847,743 |
| Solicitud de Calificaciones (RFQ) | 497 | $399,646,150 |
| Subasta Informal | 420 | $49,367,427 |
| Contrataciones por Emergencia | 303 | $41,623,651 |

El campo existe, se llena, y aun así no informa: el 66% de los contratos lleva una etiqueta
vacía.

### Una corrección importante sobre la cobertura

Una medición preliminar sobre un solo año fiscal sugería que «la mitad de los contratos no
registra el método». **Eso era un artefacto de muestrear únicamente 2023-24:**

| Año fiscal | Contratos | Con método registrado |
|---|---:|---:|
| 2023-24 | 80,137 | 49.6% |
| 2024-25 | 79,913 | **99.5%** |
| 2025-26 | 78,317 | **99.5%** |
| 2026-27 | 30,138 | **99.7%** |

El hueco de 2023-24 es real, pero **se cerró**. Generalizarlo a todo el gobierno habría sido
incorrecto. Lo mismo con «Subasta Informal» y «Contrataciones por Emergencia»: aparecían en
cero porque en 2023-24 no se usaron, pero sí existen en los años siguientes.

La función central del sitio sigue siendo el **índice de transparencia por agencia**, y el
sitio publica la cobertura por año para que nadie generalice un hueco ya cerrado.

---

## Cómo está armado

```
pipeline/     Python sin dependencias externas (solo biblioteca estándar)
  ocpr_client.py   Cliente del API de la Contralora
  catalogs.py      Catálogo de 416 agencias + enums oficiales
  ingest.py        Descarga de contratos, mes por mes, reanudable
  enrich.py        Etiqueta método de contratación y origen de fondos
  build_db.py      Construye SQLite y normaliza nombres de contratistas
  amendments.py    Baja las enmiendas de contratos de $1M o más
  export_api.py    Genera el API estático, las señales y los CSV
  run.py           Orquesta todo lo anterior

web/          Sitio estático: un index.html, SVG en línea, sin librerías
  datos/*.json              JSON pre-agregado que alimenta el dashboard
  descargas/*.csv.gz        Contratos completos, por año fiscal
  functions/api/v1/         API consultable (Cloudflare Pages Function + D1)
  _headers                  Cabeceras y política de seguridad
```

El sitio tiene tres vistas: **Los datos** (dashboard), **Buscar** (índice de contratos de
$250,000 o más, con enlace al PDF original de cada uno) y **Cómo funciona** (procedencia de
los datos, guía de uso y avisos legales).

### Por qué hay dos APIs

- **JSON estático** (`web/datos/*.json`): sin servidor, nunca se cae, cero mantenimiento.
  Alimenta el dashboard, que por eso carga al instante.
- **Pages Function + D1** (`web/functions/api/v1/`): consultas con parámetros libres, para
  periodistas y desarrolladores. Si no está montada, el sitio funciona igual.

### Enlace al documento original

Cada contrato en el buscador enlaza al PDF tal como está archivado en la Contralora:

```
https://consultacontratos.ocpr.gov.pr/contract/downloaddocument?code=<document_id>
```

Esa es la pieza más importante del proyecto. No pide que le creas al sitio: te lleva a la
fuente para que compruebes.

---

## Correr el pipeline

Requiere Python 3.11 o más reciente. No hay dependencias que instalar.

```bash
python3 pipeline/run.py          # actualización incremental
python3 pipeline/run.py --full   # rehace todo desde cero
```

Para ver el sitio localmente (hace falta un servidor por la política CORS del navegador):

```bash
cd web && python3 -m http.server 8000
```

La actualización semanal corre por GitHub Actions los domingos a las 7:00 PM de Puerto Rico
(`.github/workflows/actualizar.yml`).

## Publicar en Cloudflare Pages

El sitio está pensado para Cloudflare Pages, igual que
[observatoriosutrapr.org](https://observatoriosutrapr.org): el API queda en el mismo dominio
y el ancho de banda no es un problema si hay atención de prensa.

```bash
npm install -g wrangler && wrangler login

# Sitio
wrangler pages deploy web --project-name=dineropublicopr

# API consultable (opcional)
wrangler d1 create dineropublicopr          # pega el database_id en wrangler.toml
sqlite3 data/out/contratos.sqlite .dump > /tmp/contratos.sql
wrangler d1 execute dineropublicopr --remote --file=/tmp/contratos.sql
```

Lo práctico es conectar Cloudflare Pages al repositorio de GitHub: así cada actualización
semanal de datos se publica sola al hacer push. Para que el API consultable también se
refresque, hay que poner `CLOUDFLARE_API_TOKEN` y `CLOUDFLARE_ACCOUNT_ID` como secretos del
repositorio; sin ellos, ese paso se salta y el resto sigue funcionando.

---

## Decisiones técnicas que conviene conocer

**El API de la Contralora no está documentado.** El portal es una app ASP.NET con DataTables
del lado del servidor. `POST contract/search` devuelve JSON, protegido por un token
anti-forgery que hay que extraer del HTML y reenviar con las cookies de sesión. Si el portal
cambia de estructura, `ocpr_client.py` es el único archivo que hay que ajustar.

**`recordsFiltered` es el total real, no `recordsTotal`.** `recordsTotal` solo repite el
tamaño de la página pedida.

**El método de contratación y el origen de fondos no vienen en cada fila.** El API los acepta
como *filtro* pero no los devuelve. Para saber cómo se adjudicó cada contrato hay que
preguntar mes por mes y método por método, y quedarse con los IDs. Eso es lo que hace
`enrich.py`, y es la razón de que el pipeline tome su tiempo.

**Las fechas llegan en epoch UTC, pero Puerto Rico es AST todo el año.** Sin convertir la zona
horaria, un contrato otorgado el 1 de julio aparece el 30 de junio y cae en el año fiscal
equivocado.

**Los números de contrato no son únicos.** `2025-000001` aparece 217 veces, en municipios
distintos: cada agencia numera sus propios contratos. La llave real es el par
**(entidad, número)**, y así es como el propio portal identifica un contrato.

**Las enmiendas no vienen en el buscador.** Hay que pedirlas una por una a
`contract/getamendments`, con el par (entidad, número). Son 42,345 contratos con enmiendas,
que a una petición cada 1.5 segundos serían ~17 horas, así que el pipeline solo rastrea las
de contratos de **$1 millón o más** (~2,400 peticiones). El umbral se cambia con `--min`.

**La base SQLite no se versiona.** Pesa ~100 MB, justo el límite de archivo de GitHub, y un
binario nuevo cada semana inflaría el historial sin control. Lo que se publica es CSV
comprimido: 2.3 MB por año fiscal, y sirve para cualquier herramienta.

**El pipeline se autolimita a una petición cada 1.5 segundos.** Es un servidor público
financiado con dinero de todos; no se le hace daño para extraerle datos.

---

## Cómo leer las señales

**Ninguna señal de este sitio prueba un delito.** Son preguntas, no acusaciones.

| Señal | Qué mide | Por qué puede no significar nada |
|---|---|---|
| Índice de transparencia | % de contratos con método registrado | Puede ser descuido administrativo, no ocultamiento |
| Gasto de cierre fiscal | % del dinero anual otorgado en junio | Algunos ciclos de servicio arrancan en julio legítimamente |
| Montos bajo umbral | Contratos justo debajo de una cifra redonda | Los precios de mercado se agrupan en cifras redondas |
| Enmiendas que inflan | Enmiendas que suman más que el original | Un proyecto puede crecer por razones válidas y documentadas |

### El riesgo real del proyecto: la agrupación de nombres

La agrupación de contratistas normaliza mayúsculas, acentos y sufijos societarios (LLC, Inc.,
Corp.). Es una heurística: dos nombres agrupados son *probablemente* la misma entidad, **no
con certeza**.

Esto importa más de lo que parece. **Muchos contratos del gobierno son con personas
individuales, no con empresas**, y dos personas distintas pueden llamarse igual. Si la
herramienta dice que una persona nombrada recibió millones y la agrupación estaba mal, eso es
difamación, y la culpa sería del código, no de los datos.

Hay grupos con hasta **40 grafías distintas** detrás. Por eso:

- Cada fila publica `variantes` (cuántas grafías agrupa) y `nombres` (hasta cinco ejemplos).
- La interfaz avisa explícitamente cuando una fila agrupa más de una grafía.
- El nombre original siempre se conserva en la base y en los CSV.
- Hay un canal de corrección visible, y se atiende de buena fe.

Si vas a afirmar algo públicamente sobre una persona o empresa a partir de estos datos,
**verifica primero contra el PDF original**.

---

## Límites de cobertura

- La **Ley 122-2019** de Datos Abiertos no aplica a la Rama Legislativa, la Rama Judicial ni
  a los municipios.
- El Registro guarda los documentos públicos por **10 años** después de que vence el contrato
  y sus enmiendas.
- Aquí **no aparecen las licitaciones abiertas** que todavía no se han adjudicado. Esas están
  en [asg.pr.gov/subastas](https://asg.pr.gov/subastas) y serían una segunda fase.
- La exactitud de cada registro es responsabilidad de **la agencia que lo entró**, no de la
  Contralora ni de este proyecto.

---

## Fuentes

- [Registro de Contratos, Oficina de la Contralora](https://consultacontratos.ocpr.gov.pr/)
- [Reglamento 33 del Registro de Contratos](https://www.ocpr.gov.pr/wp-content/uploads/2024/08/Reglamento-33.pdf)
- [Ley 122-2019, Datos Abiertos](https://bvirtualogp.pr.gov/ogp/bvirtual/leyesreferencia/pdf/122-2019.pdf)
- [Ley 73-2019, Administración de Servicios Generales](https://bvirtualogp.pr.gov/ogp/Bvirtual/LeyesOrganicas/pdf/73-2019.pdf)

## Licencia

Los datos son públicos y provienen del gobierno de Puerto Rico. El código de este
repositorio es de uso libre.
