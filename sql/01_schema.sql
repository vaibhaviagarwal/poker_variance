-- Three layers.
--
--   raw       the CSVs exactly as published, every column text. Never
--             modified after load, so any cleaning rule can be changed and
--             re-run without re-downloading anything.
--   core      typed, normalised tables built from raw.
--   analysis  views holding the reconstruction and filter logic.
--
-- Re-runnable: drops and recreates everything.

DROP SCHEMA IF EXISTS analysis CASCADE;
DROP SCHEMA IF EXISTS core CASCADE;
DROP SCHEMA IF EXISTS raw CASCADE;

CREATE SCHEMA raw;
CREATE SCHEMA core;
CREATE SCHEMA analysis;

-- raw --------------------------------------------------------------------

CREATE TABLE raw.wsop_results (
    row_id       text,
    aid          text,
    competition  text,
    grid         text,
    tournament   text,
    tid          text,
    event        text,
    place        text,
    player       text,
    award        text,
    city         text,
    state        text,
    country      text
);

CREATE TABLE raw.wsop_payouts (
    row_id       text,
    aid          text,
    competition  text,
    grid         text,
    tournament   text,
    tid          text,
    event        text,
    place        text,
    award        text
);

CREATE TABLE raw.wsop_metadata (
    row_id       text,
    aid          text,
    competition  text,
    grid         text,
    tournament   text,
    tid          text,
    event        text
);

-- core -------------------------------------------------------------------

-- A series is one festival: "2024 55th Annual World Series of Poker".
CREATE TABLE core.series (
    series_id    serial PRIMARY KEY,
    name         text NOT NULL UNIQUE,
    year         int,
    is_online    boolean NOT NULL
);

CREATE TABLE core.events (
    event_id     int PRIMARY KEY,           -- the source's tid
    series_id    int NOT NULL REFERENCES core.series,
    name         text NOT NULL,
    buy_in       numeric(12, 2),            -- NULL when it cannot be parsed
    buy_in_parse text NOT NULL              -- how buy_in was obtained
        CHECK (buy_in_parse IN ('dollars', 'thousands', 'unparsed'))
);

-- Players are identified by a normalised name. That is the best the data
-- allows: there is no player ID, and two different people can share a
-- name. countries_seen records how many home countries a key has been
-- listed under, which is the visible symptom of that ambiguity.
CREATE TABLE core.players (
    player_id      serial PRIMARY KEY,
    name_key       text NOT NULL UNIQUE,
    display_name   text NOT NULL,
    countries_seen int  NOT NULL
);

-- One row per cash. Non-cashing entries are not in the source data at
-- all, which is the central limitation of the whole analysis.
CREATE TABLE core.results (
    event_id     int     NOT NULL REFERENCES core.events,
    player_id    int     NOT NULL REFERENCES core.players,
    place        int     NOT NULL CHECK (place > 0),
    payout       numeric(14, 2) NOT NULL CHECK (payout >= 0),
    country      text,
    PRIMARY KEY (event_id, player_id, place)
);

CREATE INDEX results_player_idx ON core.results (player_id);
