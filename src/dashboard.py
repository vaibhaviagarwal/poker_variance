"""Build reports/dashboard.html from Postgres and the simulation output.

    python src/dashboard.py

Every figure on the page is computed here at build time; none are typed
in. The Kelly calculator runs in the viewer's browser against a
compressed copy of the real outcome distribution, so it needs no server.
"""

from __future__ import annotations

import html
import json
import math
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///poker")

EDGE_LABELS = {"0.10": "+10% edge", "0.20": "+20% edge",
               "0.50": "+50% edge", "1.00": "+100% edge"}
EDGE_CLASS = {"0.10": "e1", "0.20": "e2", "0.50": "e3", "1.00": "e4"}

# A long professional career: 300 live events a year for 10 to 30 years.
CAREER_LO, CAREER_HI = 3_000, 9_000


def esc(x: object) -> str:
    return html.escape(str(x))


def fetch(conn: psycopg.Connection) -> dict:
    one = lambda q: conn.execute(q).fetchone()
    s = one("""SELECT entries, cashes, cash_rate, field_roi,
                      median_cash_multiple, max_multiple, top_1pct_share
               FROM analysis.outcome_summary""")
    split = conn.execute("""SELECT min_cashes, players, split_half_r
                            FROM analysis.depth_split_half""").fetchall()
    conn.execute("SELECT setseed(0.42)")
    shuffled = conn.execute("""
        WITH pc AS (SELECT player_id, nth_cash, depth FROM analysis.player_cashes),
        slots AS (SELECT player_id, nth_cash,
                         row_number() OVER (ORDER BY player_id, nth_cash) rn FROM pc),
        vals  AS (SELECT depth, row_number() OVER (ORDER BY random()) rn FROM pc),
        shuf  AS (SELECT s.player_id, s.nth_cash, v.depth
                  FROM slots s JOIN vals v USING (rn)),
        halves AS (SELECT player_id,
                          avg(depth) FILTER (WHERE nth_cash % 2 = 1) o,
                          avg(depth) FILTER (WHERE nth_cash % 2 = 0) e,
                          count(*) n FROM shuf GROUP BY player_id)
        SELECT m.k, corr(o, e) FROM halves
        CROSS JOIN (VALUES (6), (10), (20), (40)) m(k)
        WHERE n >= m.k GROUP BY m.k ORDER BY m.k""").fetchall()
    main_events = conn.execute("""
        SELECT year, round((est_from_pool + est_from_paid) / 2)::int
        FROM analysis.event_fields
        WHERE name ILIKE '%10,000 MAIN EVENT%' AND year IN (2022, 2023, 2024)
          AND NOT is_online
        ORDER BY year DESC""").fetchall()
    filt = conn.execute("SELECT outcome, events FROM analysis.exclusion_reasons").fetchall()
    shares = conn.execute("""
        WITH t AS (SELECT sum(entrants) n FROM analysis.trusted_events),
        r AS (SELECT multiple, row_number() OVER (ORDER BY multiple DESC) rk
              FROM analysis.cash_multiples),
        tot AS (SELECT sum(multiple) s FROM r)
        SELECT p.q, (SELECT sum(multiple) FROM r WHERE rk <= t.n * p.q) / tot.s
        FROM t, tot, (VALUES (0.001), (0.01), (0.05)) p(q)
        ORDER BY p.q""").fetchall()
    return {
        "entries": int(s[0]), "cashes": int(s[1]), "cash_rate": float(s[2]),
        "field_roi": float(s[3]), "median_cash": float(s[4]),
        "max_mult": float(s[5]), "top1": float(s[6]),
        "split": [(int(a), int(b), float(c)) for a, b, c in split],
        "shuffled": {int(a): float(b) for a, b in shuffled},
        "main_events": [(int(y), int(n)) for y, n in main_events],
        "filter": [(o, int(n)) for o, n in filt],
        "shares": [(float(q), float(v)) for q, v in shares],
    }


# --- the hero chart -----------------------------------------------------

W, H = 900, 450
L, R, T, B = 64, 120, 30, 88


def power_chart(sizes: list[int], curves: dict) -> str:
    lo, hi = math.log10(sizes[0]), math.log10(sizes[-1])
    pw, ph = W - L - R, H - T - B

    def x(n: float) -> float:
        return L + (math.log10(n) - lo) / (hi - lo) * pw

    def y(p: float) -> float:
        return T + (1 - p) * ph

    out = [f'<svg viewBox="0 0 {W} {H}" class="pc" role="img" '
           f'aria-label="Chance a winning record is recognised, by events played">']

    # Career band.
    out.append(f'<rect x="{x(CAREER_LO):.1f}" y="{T}" width="{x(CAREER_HI) - x(CAREER_LO):.1f}" '
               f'height="{ph}" rx="10" class="band"/>')
    # Labelled below the axis, not inside the band: every point inside the
    # band has a curve passing through it, so any label there collides.
    bx0, bx1, by = x(CAREER_LO), x(CAREER_HI), T + ph + 40
    out.append(f'<path d="M{bx0:.1f},{by - 6} V{by} H{bx1:.1f} V{by - 6}" class="bracket"/>')
    out.append(f'<text x="{(bx0 + bx1) / 2:.1f}" y="{by + 20}" class="band-t">'
               f'a whole pro career, 10–30 years</text>')

    for p in (0, 0.25, 0.5, 0.75, 1.0):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(p):.1f}" y2="{y(p):.1f}" class="g"/>')
        out.append(f'<text x="{L - 12}" y="{y(p) + 4:.1f}" class="yt">{p:.0%}</text>')
    # 80% target.
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(0.8):.1f}" y2="{y(0.8):.1f}" class="target"/>')
    out.append(f'<text x="{W - R + 8}" y="{y(0.8) + 4:.1f}" class="target-t">80% sure</text>')

    for n in sizes:
        lab = f"{n // 1000}k" if n >= 1000 else str(n)
        out.append(f'<text x="{x(n):.1f}" y="{H - B + 22}" class="xt">{lab}</text>')
    out.append(f'<text x="{L}" y="{T + ph + 60}" class="xl" text-anchor="start">'
               f'tournaments played</text>')

    for i, (key, pts) in enumerate(curves.items()):
        xs = [int(k) for k in pts]
        d = " ".join(f'{"M" if j == 0 else "L"}{x(n):.1f},{y(pts[str(n)]):.1f}'
                     for j, n in enumerate(xs))
        cls = EDGE_CLASS[key]
        out.append(f'<path d="{d}" class="ln {cls}" pathLength="1" '
                   f'style="animation-delay:{0.15 + i * 0.22:.2f}s"/>')
        for n in xs:
            p = pts[str(n)]
            out.append(f'<circle cx="{x(n):.1f}" cy="{y(p):.1f}" r="4.5" class="pt {cls}" '
                       f'data-tip="{esc(EDGE_LABELS[key])} · {n:,} events · {p:.0%}"/>')
        ln, lp = xs[-1], pts[str(xs[-1])]
        out.append(f'<text x="{x(ln) + 10:.1f}" y="{y(lp) + 5:.1f}" class="lab {cls}">'
                   f'{esc(EDGE_LABELS[key])}</text>')

    out.append("</svg>")
    return "".join(out)


def skill_chart(split: list, shuffled: dict) -> str:
    """Real vs shuffled split-half correlation, as paired bars."""
    w, h, l, b, t = 520, 250, 40, 46, 16
    maxv = 0.32
    gw = (w - l - 10) / len(split)
    bw = gw * 0.28
    yv = lambda v: t + (1 - max(v, 0) / maxv) * (h - t - b)
    base = h - b
    out = [f'<svg viewBox="0 0 {w} {h}" class="sk" role="img" '
           f'aria-label="Consistency of deep runs, real players versus shuffled">']
    for v in (0, 0.1, 0.2, 0.3):
        out.append(f'<line x1="{l}" x2="{w - 10}" y1="{yv(v):.1f}" y2="{yv(v):.1f}" class="g"/>')
        out.append(f'<text x="{l - 8}" y="{yv(v) + 4:.1f}" class="yt">{v:.1f}</text>')
    for i, (k, players, r) in enumerate(split):
        cx = l + gw * i + gw / 2
        s = shuffled.get(k, 0.0)
        out.append(f'<rect x="{cx - bw - 3:.1f}" y="{yv(r):.1f}" width="{bw:.1f}" '
                   f'height="{base - yv(r):.1f}" rx="7" class="bar-real" '
                   f'data-tip="real players · {k}+ cashes · r = {r:.2f}"/>')
        out.append(f'<text x="{cx - bw / 2 - 3:.1f}" y="{yv(r) - 7:.1f}" class="bv">{r:.2f}</text>')
        sh = max(base - yv(s), 3)
        out.append(f'<rect x="{cx + 3:.1f}" y="{base - sh:.1f}" width="{bw:.1f}" '
                   f'height="{sh:.1f}" rx="7" class="bar-shuf" '
                   f'data-tip="shuffled · {k}+ cashes · r = {s:.2f}"/>')
        out.append(f'<text x="{cx + 3 + bw / 2:.1f}" y="{base - sh - 7:.1f}" '
                   f'class="bv bv-s">{max(s, 0):.2f}</text>')
        out.append(f'<text x="{cx}" y="{base + 20}" class="xt">{k}+ cashes</text>')
        out.append(f'<text x="{cx}" y="{base + 36}" class="xs">{players:,} players</text>')
    out.append("</svg>")
    return "".join(out)


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Fredoka:wght@400;500;600;700&family=Nunito:wght@400;600;700;800&display=swap');

:root{
  color-scheme: light;
  --bg:#fff3f5; --card:#ffffff; --card-2:#fff8f9;
  --ink:#3a1d47; --ink-2:#6e5578; --ink-3:#a08aa8;
  --line:#f4dbe3; --line-2:#ead0da;
  --plum:#893894; --rose:#d4588f; --coral:#f37f4c; --butter:#f8bd57;
  --e1:#5e2370; --e2:#b03a7c; --e3:#e4683f; --e4:#e8a52c;
  --band:rgba(248,189,87,.18); --shadow:0 1px 0 rgba(137,56,148,.06), 0 14px 34px -18px rgba(137,56,148,.32);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --bg:#1b0f22; --card:#24152c; --card-2:#2b1a34;
    --ink:#f8ecf4; --ink-2:#cdb5d4; --ink-3:#927a9c;
    --line:#3a2644; --line-2:#46304f;
    --e1:#8a46aa; --e2:#d95c96; --e3:#f3875a; --e4:#f7c85a;
    --band:rgba(248,189,87,.10); --shadow:0 14px 34px -20px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --bg:#1b0f22; --card:#24152c; --card-2:#2b1a34;
  --ink:#f8ecf4; --ink-2:#cdb5d4; --ink-3:#927a9c;
  --line:#3a2644; --line-2:#46304f;
  --e1:#8a46aa; --e2:#d95c96; --e3:#f3875a; --e4:#f7c85a;
  --band:rgba(248,189,87,.10); --shadow:0 14px 34px -20px rgba(0,0,0,.7);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:400 16px/1.6 Nunito,ui-rounded,"SF Pro Rounded",system-ui,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:40px 20px 80px}
h1,h2,h3,.num{font-family:Fredoka,ui-rounded,"SF Pro Rounded",system-ui,sans-serif}

/* header */
.hello{text-align:center;padding:26px 0 8px}
.suits{display:inline-flex;gap:10px;font-size:20px;margin-bottom:14px}
.suits span:nth-child(odd){color:var(--plum)}
.suits span:nth-child(even){color:var(--rose)}
h1{font-weight:700;font-size:clamp(38px,6.4vw,64px);line-height:1.02;
   letter-spacing:-.02em;margin:0 0 14px}
.lede{max-width:58ch;margin:0 auto;color:var(--ink-2);font-size:18px}

/* chips */
.chips{display:flex;flex-wrap:wrap;justify-content:center;gap:22px;margin:38px 0 46px}
.chip{width:156px;height:156px;border-radius:50%;display:flex;flex-direction:column;
  align-items:center;justify-content:center;text-align:center;position:relative;
  background:var(--card);box-shadow:var(--shadow)}
.chip::before{content:"";position:absolute;inset:7px;border-radius:50%;
  border:4px dashed var(--c);opacity:.85}
.chip::after{content:"";position:absolute;inset:20px;border-radius:50%;
  border:1.5px solid var(--line-2)}
.chip .num{font-size:31px;font-weight:600;line-height:1;color:var(--c);position:relative;z-index:1}
.chip .cap{font-size:12.5px;line-height:1.25;color:var(--ink-2);margin-top:6px;
  max-width:96px;position:relative;z-index:1;font-weight:700}
.chip:nth-child(1){--c:var(--plum)} .chip:nth-child(2){--c:var(--rose)}
.chip:nth-child(3){--c:var(--coral)} .chip:nth-child(4){--c:#d99a1e}

/* cards */
.card{background:var(--card);border-radius:26px;padding:30px 32px;margin:0 0 22px;
  box-shadow:var(--shadow)}
.card h2{font-weight:600;font-size:27px;line-height:1.15;margin:0 0 8px;letter-spacing:-.01em}
.card > p{color:var(--ink-2);margin:0 0 18px;max-width:66ch}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:22px}
.grid2 .card{margin:0}

/* charts */
svg{width:100%;height:auto;display:block;overflow:visible}
.g{stroke:var(--line);stroke-width:1}
.yt,.xt{fill:var(--ink-3);font:700 12px Nunito,sans-serif}
.yt{text-anchor:end} .xt{text-anchor:middle}
.xs{fill:var(--ink-3);font:600 11px Nunito,sans-serif;text-anchor:middle}
.xl{fill:var(--ink-2);font:700 13px Nunito,sans-serif}
.band{fill:var(--band)}
.band-t{fill:#c98a14;font:600 14px Fredoka,sans-serif;text-anchor:middle}
.bracket{fill:none;stroke:#e0a12f;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.band-s{fill:var(--ink-3);font:700 11.5px Nunito,sans-serif;text-anchor:middle}
.target{stroke:var(--ink-3);stroke-width:1.5;stroke-dasharray:2 6;stroke-linecap:round}
.target-t{fill:var(--ink-2);font:700 12px Nunito,sans-serif}
.ln{fill:none;stroke-width:3.5;stroke-linecap:round;stroke-linejoin:round;
  stroke-dasharray:1;stroke-dashoffset:1;animation:draw 1.1s ease-out forwards}
@keyframes draw{to{stroke-dashoffset:0}}
@media (prefers-reduced-motion:reduce){.ln{animation:none;stroke-dashoffset:0}}
.pt{stroke:var(--card);stroke-width:2.5;cursor:pointer;transition:r .12s}
.pt:hover{r:7}
.lab{font:600 14px Fredoka,sans-serif}
.e1{stroke:var(--e1);fill:var(--e1)} .e2{stroke:var(--e2);fill:var(--e2)}
.e3{stroke:var(--e3);fill:var(--e3)} .e4{stroke:var(--e4);fill:var(--e4)}
.ln.e1,.ln.e2,.ln.e3,.ln.e4{fill:none}
.lab.e1,.lab.e2,.lab.e3,.lab.e4{stroke:none}
.bar-real{fill:var(--rose);cursor:pointer} .bar-shuf{fill:var(--line-2);cursor:pointer}
.bv{fill:var(--ink);font:600 12.5px Fredoka,sans-serif;text-anchor:middle}
.bv-s{fill:var(--ink-3)}
.key{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;font-size:13.5px;
  color:var(--ink-2);font-weight:700}
.key i{display:inline-block;width:14px;height:14px;border-radius:5px;
  margin-right:7px;vertical-align:-2px}

.tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--card);
  font:700 13px Nunito,sans-serif;padding:7px 12px;border-radius:12px;
  opacity:0;transform:translate(-50%,calc(-100% - 12px));transition:opacity .1s;z-index:9;
  white-space:nowrap}
.tip.on{opacity:1}

/* big facts */
.fact{display:flex;gap:20px;align-items:flex-start}
.fact .num{flex:0 0 auto;font-size:44px;font-weight:600;line-height:1;color:var(--rose)}
.fact h3{font-size:20px;font-weight:600;margin:4px 0 6px;line-height:1.2}
.fact p{margin:0;color:var(--ink-2);font-size:15px}
.shares{display:flex;flex-direction:column;gap:10px;margin-top:18px}
.share{display:grid;grid-template-columns:118px 1fr 52px;gap:12px;align-items:center;
  font-size:14px;color:var(--ink-2);font-weight:700}
.share .track{height:14px;background:var(--card-2);border-radius:99px;overflow:hidden;
  box-shadow:inset 0 0 0 1px var(--line)}
.share .fill{height:100%;border-radius:99px;
  background:linear-gradient(90deg,var(--plum),var(--rose),var(--coral))}
.share b{color:var(--ink);text-align:right;font-family:Fredoka,sans-serif;font-weight:600}

/* calculator */
.calc{display:grid;grid-template-columns:1.05fr 1fr;gap:28px;align-items:start}
.field{margin-bottom:20px}
.field label{display:flex;justify-content:space-between;font-weight:800;font-size:14.5px;
  margin-bottom:8px}
.field label output{font-family:Fredoka,sans-serif;font-weight:600;color:var(--plum);font-size:16px}
input[type=range]{width:100%;accent-color:var(--rose);height:22px}
.hint{font-size:12.5px;color:var(--ink-3);margin-top:3px;font-weight:600}
.answer{background:var(--card-2);border-radius:22px;padding:24px;text-align:center;
  box-shadow:inset 0 0 0 1.5px var(--line)}
.answer .lbl{font-weight:800;color:var(--ink-2);font-size:13.5px}
.answer .big{font-family:Fredoka,sans-serif;font-size:50px;font-weight:600;
  color:var(--plum);line-height:1.05;margin:6px 0 2px}
.answer .sub{color:var(--ink-2);font-size:14px;font-weight:700}
.answer hr{border:0;border-top:1.5px dashed var(--line-2);margin:18px 0}
.vs{display:flex;justify-content:space-around;gap:10px}
.vs div{font-size:13px;color:var(--ink-3);font-weight:700}
.vs b{display:block;font-family:Fredoka,sans-serif;font-size:21px;font-weight:600;color:var(--ink)}
.verdict{margin-top:16px;font-size:14.5px;font-weight:700;color:var(--ink);
  background:var(--card);border-radius:14px;padding:12px 14px;text-align:left}

/* method */
.method{display:grid;grid-template-columns:1fr 1fr;gap:26px}
.method h3{font-size:18px;font-weight:600;margin:0 0 8px}
.method p,.method li{color:var(--ink-2);font-size:14.5px}
.method ul{margin:0;padding-left:18px}
table{width:100%;border-collapse:collapse;font-size:14px;margin-top:6px}
th{text-align:left;color:var(--ink-3);font-weight:800;font-size:12px;padding:0 8px 8px 0}
td{padding:7px 8px 7px 0;border-top:1.5px solid var(--line);font-weight:700}
td.n{font-family:Fredoka,sans-serif;font-weight:500;font-variant-numeric:tabular-nums}
.ok{color:var(--rose)}
footer{text-align:center;color:var(--ink-3);font-size:13px;margin-top:34px;font-weight:600}
footer a{color:var(--ink-2)}

:focus-visible{outline:3px solid var(--coral);outline-offset:3px;border-radius:6px}

@media (max-width:780px){
  .grid2,.calc,.method{grid-template-columns:1fr}
  .card{padding:24px 20px;border-radius:22px}
  .chip{width:136px;height:136px} .chip .num{font-size:26px}
  .fact .num{font-size:36px}
}
"""


JS = r"""
// ---- tooltips -----------------------------------------------------
(function(){
  var tip = document.getElementById('tip');
  document.querySelectorAll('[data-tip]').forEach(function(el){
    el.addEventListener('mouseenter', function(){
      tip.textContent = el.getAttribute('data-tip'); tip.classList.add('on');
    });
    el.addEventListener('mousemove', function(e){
      tip.style.left = e.clientX + 'px'; tip.style.top = e.clientY + 'px';
    });
    el.addEventListener('mouseleave', function(){ tip.classList.remove('on'); });
  });
})();

// ---- Kelly calculator ----------------------------------------------
// Runs on a compressed copy of the real outcome distribution: a mass of
// zeros (entries that did not cash) plus a few hundred cash bins.
(function(){
  var D = window.__DIST__, P = window.__PRIOR__;
  var $ = function(id){ return document.getElementById(id); };
  var roll = $('roll'), edge = $('edge'), played = $('played');
  var nPlayed = function(){ return Math.round(Math.pow(10, +played.value)); };

  var grid = [];
  for (var i = 0; i < 180; i++) grid.push(Math.pow(10, -5 + i * (Math.log10(0.25) + 5) / 179));

  function growth(f, roi){
    var k = (1 + roi) / D.mean, g = D.zero_mass * Math.log(1 - f);
    for (var j = 0; j < D.values.length; j++)
      g += D.weights[j] * Math.log(1 - f + f * D.values[j] * k);
    return g;
  }
  function best(rois){
    var bf = 0, bg = 0;
    for (var i = 0; i < grid.length; i++){
      var s = 0;
      for (var r = 0; r < rois.length; r++) s += growth(grid[i], rois[r]);
      s /= rois.length;
      if (s > bg){ bg = s; bf = grid[i]; }
    }
    return bf;
  }
  // Standard-normal quantiles at evenly spaced probabilities.
  function zq(p){
    var a=[-39.69683028665376,220.9460984245205,-275.9285104469687,138.357751867269,-30.66479806614716,2.506628277459239],
        b=[-54.47609879822406,161.5858368580409,-155.6989798598866,66.80131188771972,-13.28068155288572],
        c=[-0.007784894002430293,-0.3223964580411365,-2.400758277161838,-2.549732539343734,4.374664141464968,2.938163982698783],
        d=[0.007784695709041462,0.3224671290700398,2.445134137142996,3.754408661907416], q, r;
    if (p < 0.02425){ q=Math.sqrt(-2*Math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
    if (p > 1-0.02425){ q=Math.sqrt(-2*Math.log(1-p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
    q=p-0.5; r=q*q; return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1);
  }
  var Z = []; for (var i = 1; i <= 25; i++) Z.push(zq(i / 26));

  var money = function(x){ return '$' + Math.round(x).toLocaleString(); };

  function update(){
    var B = +roll.value, obs = +edge.value / 100, n = nPlayed();
    $('roll-o').textContent = money(B);
    $('edge-o').textContent = (obs >= 0 ? '+' : '') + Math.round(obs * 100) + '%';
    $('played-o').textContent = n.toLocaleString();

    // How noisy a record of n events is, then how far to trust it.
    var k = (1 + obs) / D.mean;
    var sampVar = D.variance * k * k / n;
    var w = P.var / (P.var + sampVar);
    var postMean = w * obs + (1 - w) * P.mean;
    var postSd = Math.sqrt(1 / (1 / P.var + 1 / sampVar));
    var rois = Z.map(function(z){ return Math.max(postMean + z * postSd, -0.99); });

    var fNaive = best([obs]), fSafe = best(rois);
    $('safe').textContent = fSafe > 0 ? money(fSafe * B) : '$0';
    $('safe-sub').textContent = fSafe > 0
      ? 'per tournament · a ' + Math.round(1 / fSafe).toLocaleString() + ' buy-in bankroll'
      : 'sit this one out';
    $('naive').textContent = fNaive > 0 ? money(fNaive * B) : '$0';
    $('likely').textContent = (postMean >= 0 ? '+' : '') + Math.round(postMean * 100) + '%';
    $('trust').textContent = Math.round(w * 100) + '%';

    var v;
    if (fSafe === 0) v = "Your record doesn't yet beat the house cut once luck is accounted for. Keep playing smaller, or play for fun.";
    else if (fNaive > fSafe * 2) v = "Your results look great, but " + n.toLocaleString() + " events is a short record. Sizing to them would be betting on a hot streak.";
    else v = "Your record is long enough that what you see is mostly what you've got. Still, never more than the safe number.";
    $('verdict').textContent = v;
  }
  [roll, edge, played].forEach(function(el){ el.addEventListener('input', update); });
  update();
})();
"""


def main() -> None:
    curves_doc = json.loads((PROC / "power_curves.json").read_text())
    dist = json.loads((PROC / "kelly_bins.json").read_text())
    with psycopg.connect(DATABASE_URL) as conn:
        d = fetch(conn)

    sizes, curves = curves_doc["sizes"], curves_doc["curves"]
    prior = {"mean": d["field_roi"], "var": 0.25 ** 2}

    def first_reaching(key: str, target: float = 0.8) -> int | None:
        for n in sizes:
            v = curves.get(key, {}).get(str(n))
            if v is not None and v >= target:
                return n
        return None

    n20 = first_reaching("0.20")
    years20 = f"{n20 / 300:.0f}" if n20 else "100+"
    filt = dict(d["filter"])
    kept = filt.get("kept", 0)
    total_events = sum(filt.values())
    truth = {2024: 10_112, 2023: 10_043, 2022: 8_663}
    me_rows = "".join(
        f'<tr><td>{y} Main Event</td><td class="n">{n:,}</td>'
        f'<td class="n">{truth.get(y, 0):,}</td>'
        f'<td class="n ok">{abs(n / truth[y] - 1):.1%}</td></tr>'
        for y, n in d["main_events"] if y in truth
    )
    share_rows = "".join(
        f'<div class="share"><span>top {q * 100:g}% of entries</span>'
        f'<div class="track"><div class="fill" style="width:{v * 100:.1f}%"></div></div>'
        f'<b>{v:.0%}</b></div>'
        for q, v in d["shares"]
    )
    r10 = next((r for k, _, r in d["split"] if k == 10), 0)

    page = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skill or Luck?</title>
<style>{CSS}</style>
<body>
<div class="wrap">

  <header class="hello">
    <div class="suits" aria-hidden="true"><span>♠</span><span>♥</span><span>♣</span><span>♦</span></div>
    <h1>Skill or luck?</h1>
    <p class="lede">I rebuilt {d["entries"]:,} World Series of Poker tournament entries
    to find out how long it takes for a good player's results to stop looking
    like a lucky one's. The answer is: longer than a career.</p>
  </header>

  <div class="chips">
    <div class="chip"><span class="num">{d["entries"] / 1e6:.1f}M</span><span class="cap">entries rebuilt</span></div>
    <div class="chip"><span class="num">{d["cash_rate"]:.0%}</span><span class="cap">of entries win anything</span></div>
    <div class="chip"><span class="num">{d["top1"]:.0%}</span><span class="cap">of the money goes to the top 1%</span></div>
    <div class="chip"><span class="num">{d["max_mult"]:,.0f}×</span><span class="cap">biggest single result</span></div>
  </div>

  <section class="card">
    <h2>How many tournaments until you know you're good?</h2>
    <p>Each line is a player with a real, fixed edge. It shows how often their
    results would convince a statistician they're a winner, after a given number of
    events. A very good pro with a +20% edge needs about {n20:,} events to be
    80% sure, which is roughly <b>{years20} years</b> of full-time play.</p>
    {power_chart(sizes, curves)}
  </section>

  <div class="grid2">
    <section class="card">
      <h2>But skill is real</h2>
      <p>When players do cash, some reliably go deeper. I split each player's cashes
      in two and checked whether one half predicts the other. For real players it
      does, and more so with more data. With the results shuffled, it doesn't.</p>
      {skill_chart(d["split"], d["shuffled"])}
      <div class="key"><span><i style="background:var(--rose)"></i>real players</span>
      <span><i style="background:var(--line-2)"></i>shuffled (pure luck)</span></div>
    </section>

    <section class="card">
      <h2>Why it takes so long</h2>
      <div class="fact">
        <span class="num">{d["cash_rate"]:.0%}</span>
        <div><h3>Most entries win nothing</h3>
        <p>About six of every seven tournaments end with no payout at all, for good
        players and bad ones alike.</p></div>
      </div>
      <div class="fact" style="margin-top:20px">
        <span class="num">{d["median_cash"]:.1f}×</span>
        <div><h3>A typical cash barely covers it</h3>
        <p>The median payout is {d["median_cash"]:.1f} buy-ins. Profit lives in the
        rare deep runs, and they're rare for everyone.</p></div>
      </div>
      <div class="shares">{share_rows}</div>
      <p style="margin-top:14px;font-size:14px">Share of all prize money won by the
      luckiest slice of entries.</p>
    </section>
  </div>

  <section class="card" style="margin-top:22px">
    <h2>How big should your buy-in be?</h2>
    <p>The Kelly criterion picks the stake that grows a bankroll fastest. The catch:
    you never know your true edge, only your results. This calculator shrinks your
    record toward the average player by however much your sample size deserves,
    then sizes to that, using the real WSOP payout distribution.</p>
    <div class="calc">
      <div>
        <div class="field">
          <label for="roll">Bankroll <output id="roll-o"></output></label>
          <input id="roll" type="range" min="5000" max="500000" step="5000" value="100000">
        </div>
        <div class="field">
          <label for="edge">Your results so far (ROI) <output id="edge-o"></output></label>
          <input id="edge" type="range" min="-50" max="200" step="5" value="60">
          <div class="hint">profit as a share of everything you've spent on buy-ins</div>
        </div>
        <div class="field">
          <label for="played">Tournaments played <output id="played-o"></output></label>
          <input id="played" type="range" min="1.7" max="4.3" step="0.05" value="3.6">
          <div class="hint">more events, more trust in your results</div>
        </div>
      </div>
      <div class="answer">
        <div class="lbl">Safe buy-in</div>
        <div class="big" id="safe"></div>
        <div class="sub" id="safe-sub"></div>
        <hr>
        <div class="vs">
          <div>if you trusted your results<b id="naive"></b></div>
          <div>your likely true edge<b id="likely"></b></div>
          <div>trust in your record<b id="trust"></b></div>
        </div>
        <div class="verdict" id="verdict"></div>
      </div>
    </div>
  </section>

  <section class="card">
    <h2>How this was built</h2>
    <div class="method">
      <div>
        <h3>The data only shows winners</h3>
        <p>The source records who cashed, not who entered, so no player's profit can be
        known: their losing entries are invisible. I rebuilt each event's full field two
        independent ways, from the prize pool and from the number of places paid, and
        kept the {kept:,} of {total_events:,} events where the two agree.</p>
        <table>
          <tr><th>Check</th><th>Rebuilt</th><th>Actual</th><th>Off by</th></tr>
          {me_rows}
        </table>
      </div>
      <div>
        <h3>What to take with a grain of salt</h3>
        <ul>
          <li>Live events only. Online events pay a different share of the field and
          fail the cross-check.</li>
          <li>Players are matched by name; two people with the same name merge.</li>
          <li>The deep-run signal (r = {r10:.2f} at 10+ cashes) may partly reflect
          which events people choose, not only how they play.</li>
          <li>The calculator's prior, an average edge of {d["field_roi"]:+.0%} with a
          spread of ±25%, is an assumption, not a measurement.</li>
        </ul>
        <p style="margin-top:12px">Pipeline: Python and PostgreSQL. {d["cashes"]:,}
        cashes, {d["entries"]:,} reconstructed entries.</p>
      </div>
    </div>
  </section>

  <footer>Data: <a href="https://zenodo.org/records/15556775">A Dataset of Poker
  Tournaments</a>, CC BY 4.0. Every figure on this page is computed from it by
  <code>src/</code>; none are typed in.</footer>
</div>
<div class="tip" id="tip" role="status"></div>
<script>
window.__DIST__ = {json.dumps(dist)};
window.__PRIOR__ = {json.dumps(prior)};
{JS}
</script>
</body>
</html>"""

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "dashboard.html"
    out.write_text(page)
    print(f"wrote {out} ({len(page):,} bytes)")


if __name__ == "__main__":
    main()
