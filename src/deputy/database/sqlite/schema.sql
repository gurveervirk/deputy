CREATE TABLE IF NOT EXISTS branch_files (
    branch_name   TEXT NOT NULL,
    filepath      TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    last_modified REAL NOT NULL,
    PRIMARY KEY (branch_name, filepath)
);

CREATE TABLE IF NOT EXISTS entities (
    id            TEXT PRIMARY KEY,
    file_hash     TEXT NOT NULL,
    language      TEXT NOT NULL,
    full_path     TEXT NOT NULL,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entities_file_hash ON entities(file_hash);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_full_path ON entities(full_path);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache_entries (
    module_fqn     TEXT NOT NULL,
    symbol_name    TEXT NOT NULL,
    resolved_ids   TEXT NOT NULL,
    unresolved_ids TEXT NOT NULL,
    PRIMARY KEY (module_fqn, symbol_name)
);

CREATE TABLE IF NOT EXISTS cache_module_links (
    module_fqn        TEXT NOT NULL,
    cache_module_fqn  TEXT NOT NULL,
    cache_symbol_name TEXT NOT NULL,
    PRIMARY KEY (module_fqn, cache_module_fqn, cache_symbol_name)
);
