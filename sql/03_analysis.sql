-- Reconstruct what the source does not record: how many people entered.
--
-- Two independent estimates per event:
--   from the prize pool   entrants = pool / (buy_in * prize_share)
--   from places paid      entrants = cashers / paid_fraction
--
-- Validated against the 2024 Main Event (10,112 actual entrants): the
-- places-paid estimate gives 10,113 and the prize-pool estimate 10,221.
-- An event is trusted only when the two agree, which catches mis-parsed
-- buy-ins, re-entry fields and unusual payout structures without having
-- to identify each case by hand.

CREATE VIEW analysis.params AS
SELECT
    0.92::numeric AS prize_share,     -- share of entry fees paid as prizes
    0.15::numeric AS paid_fraction,   -- share of the field that cashes
    0.15::numeric AS agree_tolerance; -- max relative disagreement allowed

CREATE VIEW analysis.event_fields AS
WITH per_event AS (
    SELECT
        e.event_id,
        e.name,
        e.buy_in,
        s.name       AS series,
        s.year,
        s.is_online,
        count(*)     AS cashers,
        sum(r.payout) AS prize_pool,
        max(r.payout) AS first_prize
    FROM core.events e
    JOIN core.series s  USING (series_id)
    JOIN core.results r USING (event_id)
    GROUP BY e.event_id, e.name, e.buy_in, s.name, s.year, s.is_online
)
SELECT
    pe.*,
    pe.prize_pool / nullif(pe.buy_in * p.prize_share, 0) AS est_from_pool,
    pe.cashers / p.paid_fraction                         AS est_from_paid,
    abs(
        (pe.prize_pool / nullif(pe.buy_in * p.prize_share, 0))
        / (pe.cashers / p.paid_fraction) - 1
    )                                                    AS disagreement
FROM per_event pe
CROSS JOIN analysis.params p;

-- The events the analysis is allowed to use.
CREATE VIEW analysis.trusted_events AS
SELECT
    f.*,
    round((f.est_from_pool + f.est_from_paid) / 2)::int AS entrants
FROM analysis.event_fields f
CROSS JOIN analysis.params p
WHERE NOT f.is_online
  AND f.buy_in > 0
  AND f.disagreement <= p.agree_tolerance;

-- Why events were excluded, for the write-up.
CREATE VIEW analysis.exclusion_reasons AS
SELECT
    CASE
        WHEN f.is_online                       THEN 'online event'
        WHEN f.buy_in IS NULL                  THEN 'buy-in not in event name'
        WHEN f.disagreement > p.agree_tolerance THEN 'estimates disagree'
        ELSE 'kept'
    END AS outcome,
    count(*)        AS events,
    sum(f.cashers)  AS cashes
FROM analysis.event_fields f
CROSS JOIN analysis.params p
GROUP BY 1
ORDER BY events DESC;

-- Every cash in a trusted event, expressed in buy-ins.
CREATE VIEW analysis.cash_multiples AS
SELECT
    r.event_id,
    r.player_id,
    r.place,
    t.entrants,
    r.payout / t.buy_in                        AS multiple,
    r.place::numeric / t.entrants              AS finish_pct  -- 0 = won
FROM core.results r
JOIN analysis.trusted_events t USING (event_id);

-- Headline description of the outcome distribution. Non-cashes are not
-- rows anywhere, so they are counted from the reconstructed field size.
CREATE VIEW analysis.outcome_summary AS
WITH totals AS (
    SELECT sum(entrants) AS entries, sum(cashers) AS cashes,
           sum(prize_pool) AS paid, sum(buy_in * entrants) AS spent
    FROM analysis.trusted_events
),
ranked AS (
    SELECT multiple,
           row_number() OVER (ORDER BY multiple DESC) AS rk
    FROM analysis.cash_multiples
)
SELECT
    t.entries,
    t.cashes,
    t.cashes::numeric / t.entries                         AS cash_rate,
    t.paid / t.spent - 1                                  AS field_roi,
    (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY multiple)
       FROM analysis.cash_multiples)                      AS median_cash_multiple,
    (SELECT max(multiple) FROM analysis.cash_multiples)   AS max_multiple,
    (SELECT sum(multiple) FROM ranked WHERE rk <= t.entries * 0.01)
      / (SELECT sum(multiple) FROM ranked)                AS top_1pct_share
FROM totals t;
