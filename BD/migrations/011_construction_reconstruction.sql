CREATE TABLE IF NOT EXISTS construction_supplier_aliases (
    construction_alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_name_reported TEXT NOT NULL UNIQUE,
    supplier_name_key TEXT NOT NULL,
    supplier_rut TEXT NOT NULL,
    supplier_dv TEXT,
    canonical_name TEXT,
    alias_source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_construction_alias_name_key
    ON construction_supplier_aliases(supplier_name_key);
CREATE INDEX IF NOT EXISTS idx_construction_alias_rut
    ON construction_supplier_aliases(supplier_rut);

CREATE UNIQUE INDEX IF NOT EXISTS ux_construction_match_document
    ON construction_cost_matches(construction_item_id, document_id)
    WHERE document_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_construction_match_payment
    ON construction_cost_matches(construction_item_id, payment_id)
    WHERE payment_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_construction_matches_status
    ON construction_cost_matches(match_status);
