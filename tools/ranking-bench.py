#!/usr/bin/env python3
"""Score the local matcher against the picks actually made (match_decisions).

For every decision that chose a candidate, re-score its candidate list with hit_score and
report where the chosen one lands: top-1 accuracy, MRR, mean rank, plus the 0.90 auto-accept
threshold's precision on the same rows. Timing included so a change cannot quietly cost the
queue speed.

    python3 rank_bench.py [--ytdlp PATH_TO_ytdlp.py]
"""
import importlib.util, json, sqlite3, sys, time
from pathlib import Path

APP = Path("/var~/apps/hardlylistening")
DB = APP / "hardlylistening.db"
path = APP / "downloaders" / "ytdlp.py"
if "--ytdlp" in sys.argv:
    path = Path(sys.argv[sys.argv.index("--ytdlp") + 1])

spec = importlib.util.spec_from_file_location("ytdlp_under_test", path)
mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(APP))
spec.loader.exec_module(mod)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = con.execute("select id,title,artist,chosen_url,candidates,status from match_decisions "
                   "where chosen_url != '' order by id").fetchall()

queries = 0
hits = 0
rr = 0.0
rank_sum = 0
auto_n = auto_ok = 0
wrong_above = 0
t0 = time.perf_counter()
calls = 0

for r in rows:
    try:
        cands = json.loads(r["candidates"] or "[]")
    except ValueError:
        continue
    if len(cands) < 2:
        continue
    query = f"{r['artist']} {r['title']}".strip()
    scored = []
    for c in cands:
        if not c.get("url"):
            continue
        s = mod.hit_score(query, c)
        calls += 1
        scored.append((s, c))
    if not scored:
        continue
    scored.sort(key=lambda x: -x[0])
    urls = [c["url"] for _, c in scored]
    if r["chosen_url"] not in urls:
        continue
    rank = urls.index(r["chosen_url"]) + 1
    queries += 1
    rank_sum += rank
    rr += 1.0 / rank
    hits += rank == 1
    chosen_score = scored[rank - 1][0]
    if chosen_score >= 0.90:
        auto_n += 1
        auto_ok += rank == 1
    if rank > 1 and scored[0][0] >= 0.90:
        wrong_above += 1
elapsed = time.perf_counter() - t0

print(f"module:            {path}")
print(f"decisions used:    {queries} of {len(rows)}")
print(f"top-1 accuracy:    {hits/queries:.1%}" if queries else "no data")
print(f"MRR:               {rr/queries:.3f}" if queries else "")
print(f"mean rank:         {rank_sum/queries:.2f}" if queries else "")
print(f"auto-accept (>=.9): {auto_ok}/{auto_n} correct"
      + (f" = {auto_ok/auto_n:.1%}" if auto_n else ""))
print(f"wrong pick above a correct 0.90+: {wrong_above}")
print(f"time:              {elapsed*1000:.1f} ms for {calls} scores "
      f"= {elapsed/max(1,calls)*1e6:.0f} us/score")
