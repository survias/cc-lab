ALTER TABLE review_decisions
    ADD COLUMN payment_review_status TEXT
    CHECK (payment_review_status IN ('UNPAID_CONFIRMED'));

ALTER TABLE review_decisions
    ADD COLUMN payment_reviewed_at TEXT;

CREATE TABLE credit_note_decisions (
    credit_note_id INTEGER PRIMARY KEY REFERENCES documents(document_id),
    decision_type TEXT NOT NULL CHECK (decision_type IN ('LINKED', 'STANDALONE')),
    invoice_document_id INTEGER REFERENCES documents(document_id),
    allocated_amount_clp REAL NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (decision_type = 'LINKED' AND invoice_document_id IS NOT NULL)
        OR (decision_type = 'STANDALONE' AND invoice_document_id IS NULL)
    )
);

CREATE INDEX idx_credit_note_invoice
    ON credit_note_decisions(invoice_document_id);
