# Publicación de C&C Lab

## Decisión de arquitectura

C&C Lab escribe conciliaciones, importaciones, valores UF y decisiones en SQLite. La publicación
operativa necesita un solo proceso Streamlit y un volumen persistente con permisos de escritura.

Streamlit Community Cloud sirve por HTTPS y permite aplicaciones privadas, pero no garantiza la
persistencia del sistema de archivos local. Por eso puede usarse para una demostración de solo
lectura o una prueba desechable, no como operación definitiva de Calidad.

## Archivos que se publican

- Código, páginas, pruebas y migraciones.
- `BD/cc_lab.sqlite`, snapshot maestro inicial.
- `bidding.csv`, `revenues.csv`, los dos CSV de contratos y la plantilla de presupuesto, porque
  esta versión todavía los usa como referencias.

No se publican `BD/backups`, `BD/sources`, `H-P.xlsx`, `database2.csv`, `payments.csv`,
`suppliers.csv`, secretos, archivos WAL/SHM ni rutas personales.

## Opción operativa recomendada: Docker, disco persistente, SSO y HTTPS

1. Crear dos ubicaciones persistentes en el servidor:

   - `/srv/cc-lab/data`: SQLite y respaldos transaccionales rotatorios.
   - `/mnt/offsite/cc-lab`: almacenamiento externo o montado desde otro sistema para copias diarias.

2. Copiar `deploy/.env.example` a `deploy/.env` y completar dominio, rutas y dominio corporativo.
3. Copiar `deploy/secrets.toml.example` fuera del repositorio, completar Microsoft Entra ID y
   apuntar `CC_LAB_SECRETS_FILE` a ese archivo.
4. Dar al UID `10001` permisos de escritura sobre las dos ubicaciones persistentes.
5. Configurar DNS del dominio hacia el servidor y ejecutar desde `deploy/`:

```bash
docker compose --env-file .env up -d --build app caddy
```

El servicio `app` contiene exactamente un proceso Streamlit. Caddy obtiene y renueva HTTPS. El
acceso se valida mediante OIDC y, opcionalmente, por dominio de correo.

### Respaldo externo y rotación

Ejecutar diariamente desde cron o el programador del host:

```bash
cd /ruta/al/repositorio/deploy
docker compose --env-file .env --profile maintenance run --rm backup
```

El respaldo usa la API online de SQLite, verifica `integrity_check` antes de publicarlo y conserva
30 copias externas por defecto. Los respaldos transaccionales generados por Calidad conservan 25
copias en producción. Ambos valores se pueden cambiar con variables de entorno.

## Opción temporal: Streamlit Community Cloud

1. Crear un repositorio GitHub **privado** y confirmar con `git status --ignored` que no se agregan
   respaldos, fuentes ni secretos.
2. Subir `BD/cc_lab.sqlite` junto con el código y los archivos de referencia indicados arriba.
3. En Community Cloud seleccionar el repositorio, rama y `main.py`.
4. Mantener la aplicación privada e invitar usuarios por correo. Community Cloud entrega HTTPS.
5. Si se usa OIDC propio, cargar el contenido de `secrets.toml.example` con valores reales en la
   sección Secrets y definir `CC_LAB_AUTH_REQUIRED=true` en el entorno disponible.

Advertencia: cualquier escritura realizada en Community Cloud puede perderse al reiniciar o
reconstruir la aplicación. No usar allí Cargar período, Conciliar, Notas de crédito ni actualización
UF como proceso oficial.

## Validación previa

```bash
python -m unittest discover -s tests -v
python scripts/validate_data.py
python scripts/preflight.py
```
