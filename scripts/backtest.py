"""Backtest on the bank's own history, with no look-ahead: each closed case from the chosen window is
replayed as a fresh model-score alert, and the agent only sees closed cases opened before it.
   python scripts/backtest.py --n 300 --from 2016-09-15 --to 2016-10-31
Reports verdict accuracy against the analyst outcome and pattern accuracy on confirmed fraud.
Writes the summary to --out (default docs/backtest.json) and one row per replayed case next to it
(docs/backtest_rows.csv), so every headline number can be recomputed from the rows."""
import argparse, json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from agent.runtime import _local, Embedder, ToolBox, LLM, Investigator
from agent.answer import build as build_answer

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=300); ap.add_argument("--from", dest="start", default="2016-09-15")
ap.add_argument("--to", dest="end", default="2016-10-31"); ap.add_argument("--embedder", default="hashed")
ap.add_argument("--seed", type=int, default=7); ap.add_argument("--out", default="docs/backtest.json")
a = ap.parse_args()
emb = Embedder(a.embedder); b = _local(emb); b.agent_cases_path = os.path.join(tempfile.mkdtemp(), "x.jsonl")
os.environ.pop("GROQ_API_KEY", None); os.environ.pop("GEMINI_API_KEY", None)
inv = Investigator(ToolBox(b, emb), LLM())
cc = b.cases.copy(); cc["opened"] = pd.to_datetime(cc.opened_at)
w = cc[(cc.opened >= a.start) & (cc.opened <= a.end)]
k = a.n // 2
sample = pd.concat([w[w.outcome == "confirmed_fraud"].sample(min(k, (w.outcome == "confirmed_fraud").sum()), random_state=a.seed),
                    w[w.outcome == "cleared"].sample(min(k, (w.outcome == "cleared").sum()), random_state=a.seed)])
rows, t0 = [], time.time()
for r in sample.itertuples():
    t = str(r.txn_ids).split("|")[0].split(".")[0]
    b.as_of = r.opened
    ctx = b.txn_context(t)
    case = {"case_id": r.case_id, "opened_at": r.opened_at, "trigger_type": "risk_score", "flagged_txn_id": t, "card_id": r.card_id,
            "customer_id": r.customer_id, "risk_score": ctx.get("risk_score"), "trigger_text": "backtest replay"}
    s = inv.run(case); ans = build_answer(s)
    rows.append({"case": r.case_id, "truth": "fraud" if r.outcome == "confirmed_fraud" else "legitimate", "truth_pattern": r.pattern,
                 "verdict": ans["case"]["verdict"], "p0": s["p0"], "p1": ans["case"]["fraud_probability"], "pattern": ans["case"]["pattern"],
                 "request": bool(ans["evidence_requests"])})
df = pd.DataFrame(rows)
dec = df[df.verdict != "uncertain"]
conf = pd.crosstab(df.truth, df.verdict)
fr = df[(df.truth == "fraud") & (df.verdict == "fraud")]
# discrimination of the pre-evidence probability (independent of the simulated reply)
from itertools import product
pos, neg = df[df.truth == "fraud"].p0.values, df[df.truth == "legitimate"].p0.values
auc = sum((x > y) + 0.5 * (x == y) for x, y in product(pos, neg)) / (len(pos) * len(neg))
res = {"n": len(df), "window": [a.start, a.end], "accuracy_on_decided": round((dec.truth == dec.verdict).mean(), 3),
       "uncertain_share": round((df.verdict == "uncertain").mean(), 3), "auc_p0": round(auc, 3),
       "fraud_recall": round(((df.truth == "fraud") & (df.verdict == "fraud")).sum() / (df.truth == "fraud").sum(), 3),
       "legit_recall": round(((df.truth == "legitimate") & (df.verdict == "legitimate")).sum() / (df.truth == "legitimate").sum(), 3),
       "pattern_accuracy_on_caught_fraud": round((fr.truth_pattern == fr.pattern).mean(), 3) if len(fr) else None,
       "confusion": conf.to_dict(), "seconds": round(time.time() - t0, 1)}
os.makedirs(os.path.dirname(a.out), exist_ok=True)
json.dump(res, open(a.out, "w"), indent=2); df.to_csv(a.out.replace(".json", "_rows.csv"), index=False)
print(json.dumps(res, indent=2))
