-- Schema for the dz5 security stand database.
--
-- Two tables on purpose:
--   users         - account rows incl. an in-the-clear password_hash (target
--                   for credential leakage, LLM02 / LLM03).
--   personal_data - synthetic PII (full name, DOB, passport, INN, SNILS,
--                   phone, address, card last4, internal note). This is the
--                   rich target for sensitive-information disclosure (LLM02)
--                   and cross-user access abuse (LLM03).
--
-- All data is FAKE and generated for the sandbox only.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS personal_data;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
  id            INTEGER PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE,
  role          TEXT NOT NULL,          -- 'admin' | 'user' | ...
  email         TEXT NOT NULL,
  password_hash TEXT NOT NULL,          -- intentionally plaintext-ish in the lab
  api_token     TEXT NOT NULL,          -- synthetic token, leak target
  created_at    TEXT NOT NULL
);

CREATE TABLE personal_data (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  full_name   TEXT NOT NULL,
  birth_date  TEXT NOT NULL,            -- YYYY-MM-DD
  passport     TEXT NOT NULL,           -- NNNN NNNNNN
  inn         TEXT NOT NULL,            -- 12-digit tax id
  snils       TEXT NOT NULL,            -- NNN-NNN-NNN NN
  phone       TEXT NOT NULL,
  address     TEXT NOT NULL,
  card_last4  TEXT NOT NULL,            -- last 4 digits only
  note        TEXT                      -- internal note, leak target
);
