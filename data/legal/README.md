# Señales legales: reglas del registro curado

`senales.json` es la única parte del sitio que se mantiene a mano. Por eso tiene reglas
estrictas. Si una entrada no las cumple todas, no entra.

## La regla principal: el vínculo lo establece la fuente, no nosotros

Una entrada solo existe cuando **un documento oficial nombra a la empresa directamente**
como parte del caso: acusada, condenada, excluida o demandada por el gobierno.

- Si la acusación nombra solo a una persona, **no se publica**, aunque «sepamos» que esa
  persona es dueña de una empresa contratista. Ese salto lo haría el sitio, no la fuente, y
  es exactamente donde se cuelan los homónimos y los errores que dañan a inocentes.
- Si la fuente oficial (o la prensa citando el expediente) dice que el dueño de *esa*
  empresa fue acusado o se declaró culpable, se puede anotar en `vinculo`. **Nunca con su
  nombre.**

## Nunca se escribe el nombre de una persona natural

Ni en `entidad`, ni en `alegacion`, ni en `estatus`, ni en `vinculo`. Las fuentes enlazadas
sí traen nombres; quien quiera verlos los ve allí, en el documento oficial.

## Qué tipos entran

| `tipo` | Nivel | Qué es |
|---|---|---|
| `condena` | 1 | La empresa fue convicta. Puede activar la inhabilidad del Art. 3.4 de la Ley 2-2018 |
| `exclusion` | 1 | Exclusión federal vigente (SAM.gov, LEIE). Las automáticas las genera `exclusiones.py` |
| `acusacion` | 2 | Acusación formal (gran jurado o pliego acusatorio) que nombra a la empresa |
| `demanda_gobierno` | 3 | El gobierno estatal o federal demanda civilmente a la empresa por fraude o cobro de fondos públicos |

**No entran:** daños y perjuicios, caídas, accidentes, despidos, cobros entre privados, ni
ningún pleito que no tenga que ver con dinero público o con fraude. El piloto de octubre de
2026 lo demostró: buscar por nombre en Consulta de Casos devuelve sobre todo pleitos comunes,
y eso hace parecer sospechosa a cualquier empresa grande.

## Campos

- `entidad`: el nombre **tal como aparece en la fuente oficial**. El cruce con el Registro de
  Contratos usa la misma normalización que el resto del sitio (`normalize_name`).
- `fecha`: la fecha en que el caso se hizo público. Se usa para marcar los contratos
  otorgados después. `fecha_nota` dice qué fecha es.
- `alegacion`: qué se alega, en lenguaje de alegación («se alega», «figura entre los
  acusados»), nunca como hecho probado.
- `estatus`: lo último que se sabe, con su fuente y fecha. Si no se sabe, se dice.
- `fuentes`: al menos una con `"oficial": true` (Fiscalía federal, Departamento de Justicia,
  Rama Judicial, agencia federal). La prensa sirve para el estatus, no como única fuente.
- `verificado_el`: la fecha en que una persona abrió las fuentes y comprobó cada campo.

## Mantenimiento

- Revisa el estatus de cada entrada al menos cada tres meses. Si el caso se desestimó o la
  empresa fue absuelta, **se dice en `estatus` y se mantiene un tiempo con esa aclaración**,
  y después se retira. Una acusación desestimada no puede quedarse como bandera roja.
- Toda solicitud de corrección se atiende primero y se discute después.
