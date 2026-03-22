-- Book Reader Microservice Schema
-- Run once to initialise the database.

-- ── Users ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    avatar VARCHAR(50) DEFAULT '📚',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Books ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS books (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    author VARCHAR(255),
    filename VARCHAR(500) NOT NULL,
    storage_path VARCHAR(1000) NOT NULL,
    cover_image_path VARCHAR(1000),
    total_pages INTEGER NOT NULL DEFAULT 0,
    file_size_bytes BIGINT,
    is_scanned BOOLEAN DEFAULT FALSE,
    upload_status VARCHAR(20) DEFAULT 'pending'
        CHECK (upload_status IN ('pending', 'processing', 'ready', 'failed')),
    processing_error TEXT,
    -- Audio generation tracking
    audio_generation_status VARCHAR(20) DEFAULT 'pending'
        CHECK (audio_generation_status IN ('pending', 'in_progress', 'completed', 'failed')),
    audio_pages_completed INTEGER DEFAULT 0,
    audio_generation_started_at TIMESTAMP,
    audio_generation_completed_at TIMESTAMP,
    audio_voice_settings_hash VARCHAR(64),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Book Pages ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS book_pages (
    id SERIAL PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    text_content TEXT,
    sentences JSONB,
    word_count INTEGER DEFAULT 0,
    audio_path VARCHAR(1000),
    audio_duration_seconds FLOAT,
    audio_status VARCHAR(20) DEFAULT 'pending'
        CHECK (audio_status IN ('pending', 'generating', 'ready', 'failed')),
    audio_voice_id VARCHAR(100),
    tts_content TEXT,    -- TTS-optimised text (abbreviations expanded, symbols replaced)
    audio_timing JSONB,  -- Sentence timing: [{offset, duration}, ...]
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, page_number)
);

-- ── Reading Progress ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS book_progress (
    id SERIAL PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    current_page INTEGER DEFAULT 1,
    current_sentence INTEGER DEFAULT 0,
    playback_speed FLOAT DEFAULT 1.0,
    total_time_read_seconds INTEGER DEFAULT 0,
    last_read_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, user_id)
);

-- ── Bookmarks ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bookmarks (
    id SERIAL PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    sentence_index INTEGER,
    label VARCHAR(255),
    color VARCHAR(20) DEFAULT 'yellow',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Annotations ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS annotations (
    id SERIAL PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    highlighted_text TEXT,
    note TEXT,
    color VARCHAR(20) DEFAULT 'yellow',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Audio Generation Queue ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS book_audio_jobs (
    id SERIAL PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    status VARCHAR(20) DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'skipped', 'completed', 'failed')),
    voice_id VARCHAR(100),
    settings_hash VARCHAR(64),
    priority INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, page_number, settings_hash)
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_books_user_id ON books(user_id);
CREATE INDEX IF NOT EXISTS idx_books_upload_status ON books(upload_status);
CREATE INDEX IF NOT EXISTS idx_book_pages_book_id ON book_pages(book_id);
CREATE INDEX IF NOT EXISTS idx_book_pages_audio_status ON book_pages(audio_status);
CREATE INDEX IF NOT EXISTS idx_book_progress_user_id ON book_progress(user_id);
CREATE INDEX IF NOT EXISTS idx_book_progress_book_id ON book_progress(book_id);
CREATE INDEX IF NOT EXISTS idx_bookmarks_book_id ON bookmarks(book_id);
CREATE INDEX IF NOT EXISTS idx_bookmarks_user_id ON bookmarks(user_id);
CREATE INDEX IF NOT EXISTS idx_annotations_book_id ON annotations(book_id);
CREATE INDEX IF NOT EXISTS idx_annotations_user_id ON annotations(user_id);
CREATE INDEX IF NOT EXISTS idx_book_audio_jobs_book_id ON book_audio_jobs(book_id);
CREATE INDEX IF NOT EXISTS idx_book_audio_jobs_status ON book_audio_jobs(status);
CREATE INDEX IF NOT EXISTS idx_book_audio_jobs_priority ON book_audio_jobs(priority DESC, created_at ASC);
