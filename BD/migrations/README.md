# Migraciones

Las modificaciones del esquema SQLite se registran aquí como scripts SQL numerados, repetibles
y revisables. `schema_migrations` conserva el historial aplicado y cada migración genera un respaldo
antes de ejecutarse.

- `001_construction_costs.sql`: importaciones versionadas, costos MOP y relaciones futuras con
  documentos y pagos.
- `002_payment_import_versioning.sql`: versiones completas de H-P y trazabilidad desde cada pago
  hasta el archivo, la pestaña y la fila de origen.
- `003_master_reconciliation.sql`: mantiene la base histórica y las cargas mensuales de pagos,
  agrega el catálogo de centros y conserva decisiones y cruces manuales.
- `004_daily_uf.sql`: catálogo diario oficial de UF para documentos y pagos.
