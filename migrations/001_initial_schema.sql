-- Preppy initial schema
-- Run once against a fresh Postgres DB (idempotent via IF NOT EXISTS)

CREATE TABLE IF NOT EXISTS users (
  id               SERIAL PRIMARY KEY,
  pco_person_id    TEXT NOT NULL UNIQUE,
  pco_org_id       TEXT NOT NULL,
  name             TEXT,
  email            TEXT,
  access_token     TEXT NOT NULL,
  refresh_token    TEXT NOT NULL,
  token_expires_at TIMESTAMPTZ,
  created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS songs (
  id          SERIAL PRIMARY KEY,
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  pco_song_id TEXT,
  title       TEXT NOT NULL,
  artist      TEXT,
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS arrangements (
  id                 SERIAL PRIMARY KEY,
  song_id            INT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  pco_arrangement_id TEXT,
  name               TEXT NOT NULL,
  key                TEXT,
  bpm                TEXT,
  created_at         TIMESTAMPTZ DEFAULT now(),
  updated_at         TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sections (
  id             SERIAL PRIMARY KEY,
  arrangement_id INT NOT NULL REFERENCES arrangements(id) ON DELETE CASCADE,
  position       INT NOT NULL,
  label          TEXT NOT NULL,
  energy         TEXT,
  notes          TEXT,
  created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS setlists (
  id          SERIAL PRIMARY KEY,
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  pco_plan_id TEXT,
  name        TEXT,
  date        DATE,
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS setlist_items (
  id             SERIAL PRIMARY KEY,
  setlist_id     INT NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
  arrangement_id INT NOT NULL REFERENCES arrangements(id),
  position       INT NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_songs_user_id         ON songs(user_id);
CREATE INDEX IF NOT EXISTS idx_arrangements_song_id  ON arrangements(song_id);
CREATE INDEX IF NOT EXISTS idx_sections_arr_id       ON sections(arrangement_id);
CREATE INDEX IF NOT EXISTS idx_setlists_user_id      ON setlists(user_id);
CREATE INDEX IF NOT EXISTS idx_setlist_items_list_id ON setlist_items(setlist_id);
