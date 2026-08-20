CREATE TABLE credit_note_xml_files (
    xml_file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(source_id),
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    credit_note_document_id INTEGER REFERENCES documents(document_id),
    parsed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    parse_status TEXT NOT NULL DEFAULT 'parsed',
    parse_error TEXT
);

CREATE INDEX idx_credit_note_xml_document
    ON credit_note_xml_files(credit_note_document_id);

CREATE TABLE credit_note_xml_references (
    reference_id INTEGER PRIMARY KEY AUTOINCREMENT,
    xml_file_id INTEGER NOT NULL REFERENCES credit_note_xml_files(xml_file_id),
    credit_note_document_id INTEGER NOT NULL REFERENCES documents(document_id),
    reference_line INTEGER NOT NULL,
    referenced_document_type TEXT NOT NULL,
    referenced_folio TEXT NOT NULL,
    reference_date TEXT,
    reference_code TEXT,
    reference_reason TEXT,
    matched_document_id INTEGER REFERENCES documents(document_id),
    match_method TEXT NOT NULL,
    classification TEXT NOT NULL,
    confidence TEXT NOT NULL
);

CREATE INDEX idx_credit_note_xml_reference_note
    ON credit_note_xml_references(credit_note_document_id);

CREATE INDEX idx_credit_note_xml_reference_target
    ON credit_note_xml_references(matched_document_id);

ALTER TABLE credit_note_decisions
    ADD COLUMN source_reference_id INTEGER REFERENCES credit_note_xml_references(reference_id);

ALTER TABLE credit_note_decisions
    ADD COLUMN classification TEXT;

ALTER TABLE credit_note_decisions
    ADD COLUMN match_method TEXT;
