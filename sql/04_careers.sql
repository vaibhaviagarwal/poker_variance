-- Player-level views.
--
-- What these can and cannot say: the source records cashes only, so no
-- player's ROI is knowable — their losing entries are invisible. What IS
-- observable is how deep a player runs when they do cash. Under pure
-- luck, finishing position among the paid places is unrelated to who you
-- are. If some players reliably finish deeper, that is a skill signal,
-- and it can be measured without ever knowing how many events they
-- entered.

-- Depth among the cashers: 0 = won the event, 1 = min-cashed.
CREATE VIEW analysis.player_cashes AS
SELECT
    c.player_id,
    c.event_id,
    s.year,
    c.multiple,
    (c.place - 1)::numeric / nullif(t.cashers - 1, 0) AS depth,
    row_number() OVER (
        PARTITION BY c.player_id
        ORDER BY s.year, c.event_id
    ) AS nth_cash
FROM analysis.cash_multiples c
JOIN analysis.trusted_events t USING (event_id)
JOIN core.events e USING (event_id)
JOIN core.series s USING (series_id)
WHERE t.cashers > 1;

CREATE VIEW analysis.careers AS
SELECT
    p.player_id,
    p.display_name,
    p.countries_seen,
    count(*)                     AS cashes,
    min(pc.year)                 AS first_year,
    max(pc.year)                 AS last_year,
    sum(pc.multiple)             AS buyins_won,
    max(pc.multiple)             AS best_multiple,
    avg(pc.depth)                AS mean_depth,
    -- Share of a career's winnings from its single best result: how much
    -- of the record is one lucky night.
    max(pc.multiple) / nullif(sum(pc.multiple), 0) AS top_score_share
FROM analysis.player_cashes pc
JOIN core.players p USING (player_id)
GROUP BY p.player_id, p.display_name, p.countries_seen;

-- Split-half reliability of depth. Each qualifying player's cashes are
-- split by alternating order (1st, 3rd, 5th... vs 2nd, 4th, 6th...), so
-- the halves are interleaved in time rather than early-vs-late. If depth
-- were pure luck, a player's average in one half would say nothing about
-- the other and the correlation across players would be ~0.
CREATE VIEW analysis.depth_split_half AS
WITH halves AS (
    SELECT
        player_id,
        avg(depth) FILTER (WHERE nth_cash % 2 = 1) AS odd_depth,
        avg(depth) FILTER (WHERE nth_cash % 2 = 0) AS even_depth,
        count(*)                                   AS cashes
    FROM analysis.player_cashes
    GROUP BY player_id
)
SELECT
    m.min_cashes,
    count(*)                                         AS players,
    corr(h.odd_depth, h.even_depth)                  AS split_half_r,
    -- Spearman-Brown: reliability of the full record from the half-record
    -- correlation.
    2 * corr(h.odd_depth, h.even_depth)
      / (1 + corr(h.odd_depth, h.even_depth))        AS full_reliability
FROM halves h
CROSS JOIN (VALUES (6), (10), (20), (40)) AS m(min_cashes)
WHERE h.cashes >= m.min_cashes
GROUP BY m.min_cashes
ORDER BY m.min_cashes;
