CREATE TABLE uf_daily (
    uf_date TEXT PRIMARY KEY,
    uf_clp REAL NOT NULL CHECK (uf_clp > 0),
    source_name TEXT NOT NULL,
    source_url TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_uf_daily_imported_at ON uf_daily(imported_at);
