"""Case memory → calibrated evidence weights.
Replays the bank's closed cases from a calibration window through the same detectors (no look-ahead),
keeps the ones that were genuine model alerts (score ≥ 0.5, like the risk_score triggers), and turns
each detector's fraud rate with/without it into a smoothed log-likelihood-ratio weight.
Writes agent/calibration.json, which the agent uses for risk_score triggers.
   python scripts/calibrate.py --from 2016-07-01 --to 2016-09-14
"""
import argparse, json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from agent.runtime import _local, Embedder, ToolBox
from agent import detectors

ap = argparse.ArgumentParser()
ap.add_argument("--from", dest="start", default="2016-07-01"); ap.add_argument("--to", dest="end", default="2016-09-14")
ap.add_argument("--min_n", type=int, default=20); ap.add_argument("--out", default="agent/calibration.json")
a = ap.parse_args()
emb = Embedder("hashed"); b = _local(emb); tb = ToolBox(b, emb)
cc = b.cases.copy(); cc["opened"] = pd.to_datetime(cc.opened_at)
w = cc[(cc.opened >= a.start) & (cc.opened <= a.end)]
cl = w[w.outcome == "cleared"]; fr = w[w.outcome == "confirmed_fraud"].sample(min(len(cl), (w.outcome == "confirmed_fraud").sum()), random_state=3)
rows = []
for r in pd.concat([fr, cl]).itertuples():
    t = str(r.txn_ids).split("|")[0].split(".")[0]
    b.as_of = r.opened; ctx = b.txn_context(t)
    if (ctx.get("risk_score") or 0) < 0.5: continue
    sig, F = detectors.gather(tb, {"case_id": r.case_id, "trigger_type": "risk_score", "card_id": r.card_id}, ctx); tb.reset()
    row = {"y": int(r.outcome == "confirmed_fraud")}
    for s in sig: row[f"{s.key}{'+' if s.weight > 0 else '-'}"] = 1
    rows.append(row)
df = pd.DataFrame(rows).fillna(0)
def lo(k, n): return math.log((k + 1) / (n - k + 1))
out = {"window": [a.start, a.end], "n_alerts": len(df), "fraud_rate": round(df.y.mean(), 3), "weights": {}, "evidence": {}}
for f in [c for c in df.columns if c != "y" and not c.startswith("model_score")]:
    m = df[f] == 1; n1 = int(m.sum())
    if n1 < a.min_n: continue
    k1, k0, n0 = int(df.y[m].sum()), int(df.y[~m].sum()), int((~m).sum())
    wgt = max(min(lo(k1, n1) - lo(k0, n0), 3.0), -2.5)
    out["weights"][f] = round(wgt, 2)
    out["evidence"][f] = {"n_with": n1, "fraud_rate_with": round(k1 / n1, 3), "fraud_rate_without": round(k0 / max(n0, 1), 3)}
json.dump(out, open(a.out, "w"), indent=2)
print(json.dumps(out, indent=2))
