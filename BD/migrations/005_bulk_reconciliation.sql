CREATE TABLE allocation_rules (
    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_rut_key TEXT NOT NULL,
    item_type TEXT NOT NULL CHECK (item_type IN ('DOCUMENT', 'PAYMENT')),
    document_type INTEGER NOT NULL DEFAULT 0,
    category_code INTEGER NOT NULL,
    subcategory_code INTEGER NOT NULL,
    cost_treatment TEXT NOT NULL CHECK (cost_treatment IN ('COST', 'NON_COST', 'PENDING')),
    notes TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_code, subcategory_code)
        REFERENCES cost_centers(category_code, subcategory_code)
);

CREATE UNIQUE INDEX ux_allocation_rules_active
    ON allocation_rules(supplier_rut_key, item_type, document_type)
    WHERE is_active = 1;

CREATE TABLE reconciliation_batches (
    batch_id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL CHECK (action_type IN ('SUPPLIER', 'SELECTION')),
    item_type TEXT NOT NULL CHECK (item_type IN ('DOCUMENT', 'PAYMENT')),
    supplier_rut_key TEXT,
    record_count INTEGER NOT NULL,
    total_amount_clp REAL NOT NULL DEFAULT 0,
    category_code INTEGER,
    subcategory_code INTEGER,
    cost_treatment TEXT NOT NULL CHECK (cost_treatment IN ('COST', 'NON_COST', 'PENDING')),
    notes TEXT,
    rule_id INTEGER REFERENCES allocation_rules(rule_id),
    replaced_rule_id INTEGER REFERENCES allocation_rules(rule_id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reversed_at TEXT,
    FOREIGN KEY (category_code, subcategory_code)
        REFERENCES cost_centers(category_code, subcategory_code)
);

CREATE TABLE reconciliation_batch_items (
    batch_id TEXT NOT NULL REFERENCES reconciliation_batches(batch_id),
    item_type TEXT NOT NULL CHECK (item_type IN ('DOCUMENT', 'PAYMENT')),
    record_id INTEGER NOT NULL,
    previous_decision_exists INTEGER NOT NULL CHECK (previous_decision_exists IN (0, 1)),
    previous_category_code INTEGER,
    previous_subcategory_code INTEGER,
    previous_cost_treatment TEXT,
    previous_review_status TEXT,
    previous_notes TEXT,
    PRIMARY KEY (batch_id, item_type, record_id)
);

CREATE INDEX idx_allocation_rules_lookup
    ON allocation_rules(supplier_rut_key, item_type, document_type, is_active);

CREATE INDEX idx_reconciliation_batches_created
    ON reconciliation_batches(created_at, reversed_at);
