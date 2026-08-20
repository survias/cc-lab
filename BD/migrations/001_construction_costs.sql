CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    file_hash TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS construction_imports (
    construction_import_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_path TEXT NOT NULL,
    source_file_name TEXT NOT NULL,
    source_sheet TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    file_modified_at TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_count INTEGER NOT NULL,
    first_report_no INTEGER,
    last_report_no INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS construction_cost_items (
    construction_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    construction_import_id INTEGER NOT NULL,
    external_id TEXT NOT NULL,
    cost_sequence_no INTEGER NOT NULL,
    report_no INTEGER NOT NULL,
    source_row INTEGER NOT NULL,
    issue_date TEXT,
    invoice_number_reported TEXT,
    invoice_key TEXT,
    description TEXT,
    supplier_name_reported TEXT,
    net_amount_clp REAL NOT NULL DEFAULT 0,
    vat_amount_clp REAL NOT NULL DEFAULT 0,
    total_amount_clp REAL NOT NULL DEFAULT 0,
    net_amount_uf REAL NOT NULL DEFAULT 0,
    vat_amount_uf REAL NOT NULL DEFAULT 0,
    total_amount_uf REAL NOT NULL DEFAULT 0,
    if_observation_raw TEXT,
    survias_response_raw TEXT,
    if_observation_class TEXT NOT NULL CHECK (
        if_observation_class IN ('APPROVED_EXPLICIT', 'NO_OBSERVATION', 'OBSERVED')
    ),
    support_type TEXT NOT NULL DEFAULT 'PENDING_CLASSIFICATION' CHECK (
        support_type IN (
            'PENDING_CLASSIFICATION',
            'SII_DOCUMENT',
            'TRANSFER',
            'VALE_VISTA',
            'REMUNERATION',
            'MOP_PAYMENT',
            'LEASE',
            'OTHER_NON_TAX'
        )
    ),
    reconciliation_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
    normalized_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (construction_import_id, external_id),
    FOREIGN KEY (construction_import_id)
        REFERENCES construction_imports(construction_import_id)
);

CREATE TABLE IF NOT EXISTS construction_cost_matches (
    construction_match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    construction_item_id INTEGER NOT NULL,
    document_id INTEGER,
    payment_id INTEGER,
    match_type TEXT,
    match_method TEXT,
    match_score REAL,
    allocated_amount_clp REAL,
    allocated_amount_uf REAL,
    allocation_percentage REAL,
    match_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
    confirmed_by TEXT,
    confirmed_at TEXT,
    notes TEXT,
    FOREIGN KEY (construction_item_id)
        REFERENCES construction_cost_items(construction_item_id),
    FOREIGN KEY (document_id) REFERENCES documents(document_id),
    FOREIGN KEY (payment_id) REFERENCES payments(payment_id),
    CHECK (document_id IS NOT NULL OR payment_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_construction_import_active
    ON construction_imports(is_active)
    WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_construction_items_report
    ON construction_cost_items(construction_import_id, report_no);
CREATE INDEX IF NOT EXISTS idx_construction_items_supplier
    ON construction_cost_items(construction_import_id, supplier_name_reported);
CREATE INDEX IF NOT EXISTS idx_construction_items_invoice
    ON construction_cost_items(invoice_key);
CREATE INDEX IF NOT EXISTS idx_construction_items_observation
    ON construction_cost_items(if_observation_class);
CREATE INDEX IF NOT EXISTS idx_construction_matches_item
    ON construction_cost_matches(construction_item_id);
CREATE INDEX IF NOT EXISTS idx_construction_matches_document
    ON construction_cost_matches(document_id);
CREATE INDEX IF NOT EXISTS idx_construction_matches_payment
    ON construction_cost_matches(payment_id);
