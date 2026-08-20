DROP INDEX IF EXISTS ux_payment_imports_active;

ALTER TABLE payment_imports
    ADD COLUMN source_period TEXT;

ALTER TABLE payment_imports
    ADD COLUMN import_mode TEXT NOT NULL DEFAULT 'snapshot';

UPDATE payment_imports
SET source_period = COALESCE(source_period, 'HISTORICO'),
    import_mode = 'baseline'
WHERE payment_import_id = (
    SELECT MIN(payment_import_id) FROM payment_imports WHERE is_active = 1
);

CREATE INDEX idx_payment_imports_active_period
    ON payment_imports(is_active, source_period);

CREATE UNIQUE INDEX ux_payment_imports_monthly_period
    ON payment_imports(source_period)
    WHERE is_active = 1 AND import_mode = 'monthly';

CREATE TABLE cost_centers (
    category_code INTEGER NOT NULL,
    subcategory_code INTEGER NOT NULL,
    category_name TEXT NOT NULL,
    subcategory_name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    PRIMARY KEY (category_code, subcategory_code)
);

INSERT INTO cost_centers(category_code, subcategory_code, category_name, subcategory_name) VALUES
    (100, 101, 'EPC - Contractor', 'Advance Payment'),
    (100, 102, 'EPC - Contractor', 'PID (Detailed Engineering Project)'),
    (100, 103, 'EPC - Contractor', 'ITS (Intelligent Transport Systems)'),
    (100, 104, 'EPC - Contractor', 'Construction Toll Plaza'),
    (100, 105, 'EPC - Contractor', 'Expropriation & Land Acquisition'),
    (100, 106, 'EPC - Contractor', 'Utility Relocation Works'),
    (100, 107, 'EPC - Contractor', 'Environmental Studies'),
    (100, 108, 'EPC - Contractor', 'EPC Works'),
    (200, 201, 'Maintenance', 'Routine Maintenance'),
    (200, 202, 'Maintenance', 'Major Maintenances'),
    (200, 203, 'Maintenance', 'RM-MM'),
    (200, 204, 'Maintenance', 'Maintenance Studies'),
    (200, 205, 'Maintenance', 'Maintenance Others Works'),
    (300, 301, 'Operations', 'Operations'),
    (300, 302, 'Operations', 'Backoffice'),
    (400, 401, 'SPV', 'Administration Department'),
    (400, 402, 'SPV', 'Others'),
    (400, 403, 'SPV', 'Legal Department'),
    (400, 404, 'SPV', 'HR Department'),
    (400, 405, 'SPV', 'Finance Department'),
    (400, 406, 'SPV', 'Economic Department'),
    (400, 407, 'SPV', 'Utilities'),
    (400, 408, 'SPV', 'IGYC Costs'),
    (500, 501, 'MOP', 'Revenue Sharing'),
    (500, 502, 'MOP', 'Contract Management Fees'),
    (500, 503, 'MOP', 'Franchise Fees'),
    (500, 504, 'MOP', 'Payment for pre-existing infrastructure'),
    (500, 505, 'MOP', 'Others MOP'),
    (600, 601, 'Financing Cost', 'Long-term Loan'),
    (600, 602, 'Financing Cost', 'Short-term Loan'),
    (600, 603, 'Financing Cost', 'Other Financing Fees'),
    (700, 701, 'Insurance & Guarantee', 'Insurance'),
    (700, 702, 'Insurance & Guarantee', 'Guarantee'),
    (800, 801, 'Tax', 'VAT'),
    (800, 802, 'Tax', 'Income Tax'),
    (800, 803, 'Tax', 'Municipal Patent'),
    (800, 804, 'Tax', 'Other Tax'),
    (900, 901, 'SPV Others', 'SPV Other'),
    (900, 902, 'SPV Others', 'IT'),
    (900, 903, 'SPV Others', 'Technical Advisory'),
    (900, 904, 'SPV Others', 'PMO'),
    (1000, 1001, 'EPC - SPV', 'IF Office Expenses'),
    (1000, 1002, 'EPC - SPV', 'Citizen Studies'),
    (1000, 1003, 'EPC - SPV', 'Construction Department Works');

CREATE TABLE review_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL CHECK (item_type IN ('DOCUMENT', 'PAYMENT')),
    document_id INTEGER REFERENCES documents(document_id),
    payment_id INTEGER REFERENCES payments(payment_id),
    category_code INTEGER,
    subcategory_code INTEGER,
    cost_treatment TEXT NOT NULL DEFAULT 'COST'
        CHECK (cost_treatment IN ('COST', 'NON_COST', 'PENDING')),
    review_status TEXT NOT NULL DEFAULT 'RESOLVED'
        CHECK (review_status IN ('PENDING', 'RESOLVED')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (item_type = 'DOCUMENT' AND document_id IS NOT NULL AND payment_id IS NULL)
        OR
        (item_type = 'PAYMENT' AND payment_id IS NOT NULL AND document_id IS NULL)
    ),
    FOREIGN KEY (category_code, subcategory_code)
        REFERENCES cost_centers(category_code, subcategory_code)
);

CREATE UNIQUE INDEX ux_review_decision_document
    ON review_decisions(document_id)
    WHERE document_id IS NOT NULL;

CREATE UNIQUE INDEX ux_review_decision_payment
    ON review_decisions(payment_id)
    WHERE payment_id IS NOT NULL;

CREATE TABLE manual_matches (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(document_id),
    payment_id INTEGER NOT NULL REFERENCES payments(payment_id),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (document_id, payment_id)
);

CREATE INDEX idx_manual_matches_document ON manual_matches(document_id);
CREATE INDEX idx_manual_matches_payment ON manual_matches(payment_id);
