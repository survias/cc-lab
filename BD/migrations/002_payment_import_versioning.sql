CREATE TABLE payment_imports (
    payment_import_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_path TEXT NOT NULL,
    source_file_name TEXT NOT NULL,
    source_hash TEXT NOT NULL UNIQUE,
    source_modified_at TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_count INTEGER NOT NULL,
    valid_row_count INTEGER NOT NULL,
    invalid_row_count INTEGER NOT NULL,
    first_payment_date TEXT,
    last_payment_date TEXT,
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    notes TEXT
);

CREATE UNIQUE INDEX ux_payment_imports_active
    ON payment_imports(is_active)
    WHERE is_active = 1;

ALTER TABLE sources
    ADD COLUMN payment_import_id INTEGER REFERENCES payment_imports(payment_import_id);

ALTER TABLE payments_raw
    ADD COLUMN payment_import_id INTEGER REFERENCES payment_imports(payment_import_id);

ALTER TABLE payments
    ADD COLUMN payment_import_id INTEGER REFERENCES payment_imports(payment_import_id);

ALTER TABLE validation_issues
    ADD COLUMN payment_import_id INTEGER REFERENCES payment_imports(payment_import_id);

CREATE INDEX idx_sources_payment_import
    ON sources(payment_import_id);

CREATE INDEX idx_payments_raw_import
    ON payments_raw(payment_import_id, source_sheet, source_row);

CREATE UNIQUE INDEX ux_payments_import_row
    ON payments(payment_import_id, source_sheet, source_row)
    WHERE payment_import_id IS NOT NULL;

CREATE INDEX idx_validation_payment_import
    ON validation_issues(payment_import_id);
