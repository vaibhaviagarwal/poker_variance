-- Build core from raw. All parsing rules live here.

-- Money strings ("$408,468") to numbers. Anything that is not a clean
-- amount after stripping becomes NULL rather than a guessed value.
CREATE OR REPLACE FUNCTION core.parse_money(s text) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN regexp_replace(s, '[^0-9.]', '', 'g') ~ '^[0-9]+(\.[0-9]+)?$'
        THEN regexp_replace(s, '[^0-9.]', '', 'g')::numeric
    END
$$;

-- Player name to matching key: lower case, whitespace collapsed,
-- non-breaking spaces and stray punctuation at the edges removed.
CREATE OR REPLACE FUNCTION core.name_key(s text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT nullif(
        btrim(regexp_replace(lower(replace(s, chr(160), ' ')), '\s+', ' ', 'g'),
              ' .,;'),
        '')
$$;

-- series ----------------------------------------------------------------

INSERT INTO core.series (name, year, is_online)
SELECT DISTINCT
    tournament,
    substring(tournament from '((?:19|20)[0-9]{2})')::int,
    tournament ILIKE '%online%'
FROM raw.wsop_results
WHERE tournament IS NOT NULL;

-- events ----------------------------------------------------------------
-- The buy-in is embedded in the event name: "Event #1: $5,000 Champions
-- Reunion ...". A "K" suffix means thousands ("$10K"), and missing that
-- would read a $10,000 event as $10.

INSERT INTO core.events (event_id, series_id, name, buy_in, buy_in_parse)
SELECT
    e.tid::int,
    s.series_id,
    e.event,
    CASE
        WHEN m.amount IS NULL THEN NULL
        WHEN m.k_suffix THEN m.amount * 1000
        ELSE m.amount
    END,
    CASE
        WHEN m.amount IS NULL THEN 'unparsed'
        WHEN m.k_suffix THEN 'thousands'
        ELSE 'dollars'
    END
FROM (
    SELECT DISTINCT ON (tid) tid, event, tournament
    FROM raw.wsop_results
    ORDER BY tid
) e
JOIN core.series s ON s.name = e.tournament
CROSS JOIN LATERAL (
    SELECT
        nullif(replace(substring(e.event from '\$\s?([0-9][0-9,]*(?:\.[0-9]+)?)'), ',', ''), '')::numeric
            AS amount,
        e.event ~ '\$\s?[0-9][0-9,.]*\s?[Kk]\M' AS k_suffix
) m;

-- players ---------------------------------------------------------------

INSERT INTO core.players (name_key, display_name, countries_seen)
SELECT
    core.name_key(player),
    mode() WITHIN GROUP (ORDER BY player),
    count(DISTINCT nullif(country, ''))
FROM raw.wsop_results
WHERE core.name_key(player) IS NOT NULL
GROUP BY core.name_key(player);

-- results ---------------------------------------------------------------
-- ON CONFLICT drops exact duplicate rows (same player, event and place)
-- rather than failing the load; the count of drops is reported by load.py.

INSERT INTO core.results (event_id, player_id, place, payout, country)
SELECT
    r.tid::int,
    p.player_id,
    r.place::int,
    core.parse_money(r.award),
    nullif(r.country, '')
FROM raw.wsop_results r
JOIN core.players p ON p.name_key = core.name_key(r.player)
WHERE core.parse_money(r.award) IS NOT NULL
ON CONFLICT DO NOTHING;

ANALYZE core.series;
ANALYZE core.events;
ANALYZE core.players;
ANALYZE core.results;
