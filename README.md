# C&C Lab

Herramienta interna de control de costos de Survías. `BD/cc_lab.sqlite` es la base maestra de
documentos, pagos, conciliaciones y decisiones realizadas desde Streamlit.

## Funcionalidad recuperada

- Costos SII por centro, proveedor y documento en CLP y UF oficial diaria.
- Estado visual de documentos pagados, no pagados y cruces ambiguos.
- Estado de cuenta por proveedor y resumen por centro de costo.
- Gráficos anuales y mensuales por moneda y estado de pago.
- Bidding versus costo real pagado, ingresos y desglose por proveedor.
- Contratos activos, adicionales y documentos asociados.
- Costos de construcción informados al MOP por informe, proveedor, partida y observación IF.

## Controles nuevos conservados

- SQLite local como base maestra; las escrituras se concentran en Calidad.
- RCV SII con filtros, montos económicos y trazabilidad a la fila original.
- Calidad de datos, carga mensual y conciliación desde una sola página.
- Cruces parametrizados y casos ambiguos identificados como `Revisar cruce`.
- Regla de folio base `A/B` reservada exclusivamente para el RUT `59.296.220-9`.
- Importaciones MOP versionadas e idempotentes por SHA-256, con respaldo previo de SQLite.
- H-P histórico más archivos mensuales incrementales, todos versionados por SHA-256.
- Catálogo UF diario dentro de SQLite, actualizado desde Calidad y contrastado con los pagos históricos.

La carga mensual se realiza en `Calidad`: un RCV SII y un archivo de pagos por período. La
aplicación valida ambos antes de escribir, crea un respaldo y rechaza períodos o archivos repetidos.

## Estructura

La aplicación completa vive en `00_New`:

```text
00_New/
├── main.py
├── pages/
├── utils/
├── scripts/
├── BD/
├── images/
├── tests/
├── requirements.txt
└── README.md
```

La única fuente operativa de la aplicación es:

- `cc_lab.sqlite`

`H-P.xlsx`, `database2.csv` y `payments.csv` se conservan como respaldo histórico, pero ninguna
pantalla operativa los consulta. Bidding, ingresos y contratos siguen usando archivos de referencia
hasta su etapa de migración.

## Flujo mensual

1. Abrir `Calidad > Cargar período`.
2. Elegir año y mes.
3. Subir un RCV SII CSV/XLSX y un archivo mensual de pagos CSV/XLSX.
4. Revisar la vista previa y confirmar la importación.
5. Resolver los casos en `Pendientes` y `Conciliar`.
6. Revisar en `UF` que la cobertura diaria esté vigente y sin fechas faltantes.

Los pagos mensuales deben contener una sola tabla y solo fechas del período elegido. No se vuelve
a cargar el H-P histórico completo. Las decisiones manuales se guardan en SQLite sin alterar las
filas originales.

## Valores UF

`uf_daily` conserva en SQLite el valor oficial por fecha. Los documentos se convierten con la UF
de emisión y los pagos con la UF del desembolso. Calidad permite actualizar la tabla desde el SII;
CLP permanece disponible y nunca se reemplazan los montos originales.

## Ejecución

Desde `00_New`:

```bash
../.venv/bin/streamlit run main.py --server.address 127.0.0.1
```

Las rutas de producción se configuran con variables de entorno:

- `CC_LAB_DATABASE_PATH`: SQLite ubicado en un volumen persistente.
- `CC_LAB_BACKUP_DIR`: respaldos transaccionales rotatorios.
- `CC_LAB_BACKUP_MIRROR_DIR`: destino externo para el respaldo diario.
- `CC_LAB_AUTH_REQUIRED`: activa autenticación OIDC corporativa.
- `CC_LAB_ALLOWED_EMAIL_DOMAINS`: dominios autorizados, separados por coma.

Las fuentes históricas opcionales para reconstrucciones excepcionales se configuran mediante
`CC_LAB_CREDIT_NOTE_XML_SOURCE`, `CC_LAB_CONSTRUCTION_SOURCE`, `CC_LAB_ORIGINAL_DATABASE` y
`CC_LAB_PAYMENTS_SOURCE`. La operación normal no depende de ellas.

## Importación de costos de construcción

La ruta oficial se utiliza por defecto y también puede entregarse como argumento:

```bash
../.venv/bin/python scripts/import_construction_costs.py
../.venv/bin/python scripts/import_construction_costs.py /ruta/al/consolidado.xlsx
```

El importador lee exclusivamente `Consolidado definitivo`, excluye la fila Total y nunca modifica
el Excel. Cada versión queda registrada en `construction_imports`; Streamlit consulta solo la activa.

## Importación histórica de pagos

`BD/H-P.xlsx` conserva la carga inicial histórica. Este comando se mantiene únicamente para
reconstruir esa base; las incorporaciones posteriores se hacen por mes desde Calidad.

```bash
../.venv/bin/python scripts/import_payments.py
```

## Verificación

```bash
../.venv/bin/python scripts/validate_data.py
../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python scripts/preflight.py
```

## Publicación

La guía completa está en [`DEPLOYMENT.md`](DEPLOYMENT.md). Incluye:

- un solo proceso Streamlit;
- SQLite y respaldos en almacenamiento persistente;
- autenticación corporativa con OIDC/Microsoft Entra ID;
- HTTPS mediante Caddy;
- respaldo externo verificado y con rotación;
- exclusión de los 11 GB de `BD/backups` y de las fuentes históricas.

Streamlit Community Cloud sirve por HTTPS y puede proteger una aplicación privada, pero no
garantiza persistencia del disco local. Solo debe usarse como demostración temporal si las
escrituras de Calidad no son oficiales.

## Alcance pendiente

Bidding, ingresos y contratos permanecen como referencias históricas hasta su siguiente etapa.
