# Database & Storage Schemas

All persistent data lives under `%APPDATA%\Copilot\` (Windows) or `~/.copilot/` (dev fallback).

## Directory Layout

```
%APPDATA%/Copilot/
├── .env                 # optional secrets override
├── logs/
│   └── copilot.log      # rotating 5×5 MB
├── data/
│   ├── copilot.db       # SQLite (WAL)
│   ├── .master.key      # Fernet key (0600)
│   ├── settings.json    # non-secret UI prefs
│   └── rag_index.json   # local NumPy/hash vector index (no C++)
├── documents/           # uploaded resume / JD / notes
└── cache/
```

> ChromaDB under `chroma/` is optional. If `chromadb` is not installed (common on
> Python 3.14 without MSVC), the app uses `data/rag_index.json` only.
## Encryption

- Algorithm: **Fernet** (AES-128-CBC + HMAC-SHA256) via `cryptography`
- Encrypted columns: session titles/notes, message bodies, settings values
- Key file: `data/.master.key` — losing it makes history undecryptable (by design)

## SQLite Schema

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,
    title_enc   BLOB NOT NULL,      -- Fernet(title)
    company_enc BLOB,               -- Fernet(company)
    role_enc    BLOB,               -- Fernet(role)
    notes_enc   BLOB,               -- Fernet(notes)
    started_at  TEXT NOT NULL,      -- ISO-8601 UTC
    ended_at    TEXT,
    meta_json   TEXT DEFAULT '{}'
);

CREATE TABLE messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,      -- user|assistant|transcript|system
    speaker     TEXT DEFAULT '',    -- interviewer|candidate|''
    source      TEXT DEFAULT '',    -- audio|ocr|manual|ai
    content_enc BLOB NOT NULL,      -- Fernet(content)
    created_at  TEXT NOT NULL,
    meta_json   TEXT DEFAULT '{}'
);
CREATE INDEX idx_messages_session ON messages(session_id, created_at);

CREATE TABLE documents (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    path         TEXT NOT NULL,
    doc_type     TEXT NOT NULL,      -- resume|jd|pdf|notes
    chunk_count  INTEGER DEFAULT 0,
    checksum     TEXT DEFAULT '',
    indexed_at   TEXT NOT NULL
);

CREATE TABLE settings (
    key         TEXT PRIMARY KEY,
    value_enc   BLOB NOT NULL,      -- Fernet(value)
    updated_at  TEXT NOT NULL
);

CREATE TABLE embedded_paths (
    key         TEXT PRIMARY KEY,   -- e.g. tesseract_exe, tessdata
    path        TEXT NOT NULL,
    description TEXT DEFAULT '',
    updated_at  TEXT NOT NULL
);
```

## Chroma / Local Collection

- Default: `data/rag_index.json` with hash embeddings + cosine search (no native build)
- Optional: Chroma collection `interview_docs` when `chromadb` is installed
- Metadata per chunk: `doc_id`, `filename`, `doc_type`, `chunk`

## settings.json (plain)

Non-secret UI/runtime preferences only — **API keys never written here**.

```json
{
  "audio": { "sample_rate": 16000, "vad_aggressiveness": 2 },
  "ocr": { "poll_interval_ms": 80, "change_threshold": 0.018 },
  "ai": { "gemini_model": "gemini-1.5-flash", "stream": true },
  "rag": { "top_k": 4, "chunk_size": 800 },
  "ui": { "opacity": 0.92, "stealth_enabled": true, "hotkey_toggle": "alt+h" },
  "log_level": "INFO"
}
```
