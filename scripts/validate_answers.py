"""Checks every answer file against the README answer format and the dataset IDs."""
import glob, json, sys
import pandas as pd
tx = pd.read_parquet("data/local/transactions.parquet", columns=["TransactionID", "card_id", "customer_id"])
TX = set(tx.TransactionID.astype(str)); CARDS = set(tx.card_id); CUST = set(tx.customer_id)
CC = set(pd.read_parquet("data/local/closed_cases.parquet").case_id)
ACTIONS = {"ALLOW_TRANSACTION","DECLINE_TRANSACTION","MONITOR_CARD","MONITOR_CONNECTED_CARDS","WARN_CUSTOMER","VERIFY_WITH_CUSTOMER","STEP_UP_AUTH",
           "BLOCK_CARD","BLOCK_ALL_CARDS","GENERATE_REPORT","CREATE_CASE","FILE_REPORT","ESCALATE_TO_ANALYST","CLOSE_NO_FRAUD"}
PAT = {"card_testing","card_not_present_fraud","card_not_present_new_device","out_of_region_use","account_takeover","undocumented","none"}
errs = []
files = sorted(glob.glob("cases/HHG-*.json"))
for f in files:
    a = json.load(open(f)); c = a["case"]; e = lambda m: errs.append(f"{a['case_id']}: {m}")
    for k in ["case_id","case","evidence_requests","next_best_actions","sar","stop_reason","tool_calls","tokens","latency_s"]:
        if k not in a: e(f"missing {k}")
    if c["verdict"] not in ("fraud","legitimate","uncertain"): e("verdict")
    if c["status"] not in ("open","closed_fraud","closed_legitimate","escalated"): e("status")
    if c["pattern"] not in PAT: e("pattern")
    if c["pattern"] == "undocumented" and not c["pattern_description"]: e("undocumented needs description")
    for t in c["affected_txn_ids"] + ([c["first_suspicious_txn_id"]] if c["first_suspicious_txn_id"] else []):
        if t not in TX: e(f"unknown txn {t}")
    for k in c["connected_card_ids"]:
        if k not in CARDS: e(f"unknown card {k}")
    for k in c["similar_prior_cases"]:
        if k not in CC: e(f"unknown closed case {k}")
    if c["verdict"] == "legitimate" and (c["affected_txn_ids"] or c["exposure_usd"] or a["sar"]["file"]): e("legitimate must have no txns/exposure/SAR")
    nba = a["next_best_actions"]
    for a2 in nba["initial"] + nba["final"]:
        if a2["action"] not in ACTIONS: e(f"bad action {a2['action']}")
        if a2["route"] not in ("auto","L1","L2"): e("bad route")
    fr = any(x["action"] == "FILE_REPORT" for x in nba["final"])
    if fr != a["sar"]["file"]: e("sar.file disagrees with FILE_REPORT in final")
    if a["sar"]["file"] and not a["sar"]["narrative"]: e("sar narrative missing")
    if not a["evidence_requests"] and nba["initial"] != nba["final"]: e("no request but initial != final")
    for s in a["sar"]["subjects"]:
        if not (s in CARDS or s in CUST or s.startswith("D")): e(f"unknown subject {s}")
print(f"{len(files)} files checked, {len(errs)} problems"); [print(" -", x) for x in errs]
if errs: sys.exit(1)

# ---- stricter checks: IDs inside evidence/subjects, rule citations, text lengths ----
import re as _re
errs2 = []
def sentences(t): return len([x for x in _re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", t.strip()) if x])
for f in files:
    a = json.load(open(f)); cid = a["case_id"]
    known = TX | CARDS | CUST | CC
    for e in a["case"]["evidence"]:
        for i in e["entity_ids"]:
            if i not in known: errs2.append(f"{cid}: evidence entity {i} not in dataset")
    for s_ in a["sar"]["subjects"]:
        if s_ not in known: errs2.append(f"{cid}: SAR subject {s_} not in dataset")
    for x in a["next_best_actions"]["initial"] + a["next_best_actions"]["final"]:
        if not _re.search(r"\bR\d+\b|[Ss]ection \d", x["reason"]): errs2.append(f"{cid}: reason without rule citation: {x['reason'][:60]}")
    if not _re.search(r"\bR\d+\b|[Ss]ection \d", a["sar"]["reason"]): errs2.append(f"{cid}: sar.reason without rule citation")
    n = sentences(a["case"]["summary"])
    if not 2 <= n <= 6: errs2.append(f"{cid}: summary has {n} sentences (want 2-6)")
    if a["sar"]["file"]:
        n = sentences(a["sar"]["narrative"])
        if not 6 <= n <= 12: errs2.append(f"{cid}: SAR narrative has {n} sentences (want 6-12)")
    if set(a) != {"case_id","case","evidence_requests","next_best_actions","sar","stop_reason","tool_calls","tokens","latency_s"}: errs2.append(f"{cid}: top-level keys differ from spec")
print(f"strict checks: {len(errs2)} problems"); [print(" -", x) for x in errs2]
sys.exit(1 if errs2 else 0)
