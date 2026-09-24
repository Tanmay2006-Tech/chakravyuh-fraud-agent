"""Builds the graded answer file (README 'Answer Format') from the final workflow state."""
import re

ID_OK = re.compile(r"^(\d{7}|C\d{5}|C\d{5}-K\d|CC-\d{4})$")

def dataset_ids(ids):
    """Only IDs that exist in the dataset (transactions, customers, cards, closed cases).
    Device profiles have no dataset ID, so they are named in the claim text instead."""
    out = []
    for i in ids:
        i = str(i)
        if ID_OK.match(i) and i not in out: out.append(i)
    return out

def evidence_items(s):
    ev = []
    for x in sorted(s["signals"], key=lambda x: -abs(x.weight)):
        ev.append({"claim": x.claim, "source": x.source, "ref": x.ref, "entity_ids": dataset_ids(x.entity_ids)})
    eps = s["episode"]
    if s["verdict"] == "fraud" and eps:
        ev.append({"claim": f"Fraud episode: {len(eps)} transaction(s) on {s['case']['card_id']} from {eps[0]['ts']:%Y-%m-%d %H:%M} to {eps[-1]['ts']:%Y-%m-%d %H:%M}, "
                            f"${sum(x['amount'] for x in eps):,.2f} in total ({', '.join('$' + format(x['amount'], ',.2f') for x in eps[:6])}{'…' if len(eps) > 6 else ''})",
                   "source": "graph", "ref": "query:fingerprint_history / card_window", "entity_ids": [str(x["id"]) for x in eps]})
    for f in s.get("follow_up", []):
        if f.get("fraud_cases"):
            ev.append({"claim": f"Linked card {f['card']} already has confirmed-fraud case(s) {', '.join(f['fraud_cases'])}",
                       "source": "graph", "ref": f"query:card_cases(card={f['card']})", "entity_ids": [f["card"]] + f["fraud_cases"]})
    if s["case"]["trigger_type"] == "customer_report":
        ev.append({"claim": f"Customer report: {s['case']['trigger_text'].split('message: ')[-1]}", "source": "customer",
                   "ref": f"case_pack:{s['case']['case_id']}", "entity_ids": [str(s['case']['flagged_txn_id'])]})
    for i, r in enumerate(s.get("requests", []), 1):
        ev.append({"claim": f"Assumed response to {r['type']}: {r['assumed_response']}",
                   "source": "customer" if r["type"] != "analyst_info" else "external", "ref": f"evidence_request:{i}", "entity_ids": []})
    rules = sorted({r for a in s["initial"] + s["final"] for r in re.findall(r"R\d+", a["reason"])}, key=lambda r: int(r[1:]))
    for d in s.get("docs", []):
        did = str(d["id"])
        if did.startswith("policy:") and did.split(":")[1] in rules + ["3a"] or did == f"pattern:{s['pattern']}":
            ev.append({"claim": f"Retrieved guidance applied: {passage(d.get('content', ''))}", "source": "document",
                       "ref": d.get("title", did), "entity_ids": []})
    return ev

def rule_of(reason):
    m = re.search(r"R\d+|[Ss]ection \d+[a-z]?", reason)
    return m.group(0) if m else "policy"

def what_changed(s):
    """One or two sentences: what came back, how the probability moved, and which actions changed under which rule."""
    if s["final"] == s["initial"]: return "nothing"
    ia = {a["action"] for a in s["initial"]}; fa = {a["action"] for a in s["final"]}
    resp = (s["response"][1] if s["response"] else "Further evidence arrived").rstrip(".")
    p0, p1 = s["p0"], s["p1"]
    prob = f"the probability stayed at {p1:.2f}" if round(p0, 2) == round(p1, 2) else f"the probability moved from {p0:.2f} to {p1:.2f}"
    if s["response"] and s["response"][0] == "no_reply": prob += " (silence is not evidence either way)"
    added = [f"{a['action']} ({rule_of(a['reason'])})" for a in s["final"] if a["action"] not in ia]
    dropped = sorted(ia - fa)
    return (f"{resp}. As a result {prob}; added {', '.join(added) or 'no actions'}"
            + (f"; no longer needed: {', '.join(dropped)}" if dropped else "") + ".")

def passage(text, n=260):
    """A retrieved policy/pattern passage cut at a sentence (or word) boundary, never mid-word."""
    text = " ".join(str(text).split())
    if len(text) <= n: return text
    cut = text[:n]; end = max(cut.rfind(". "), cut.rfind("; "))
    return cut[:end + 1] if end > n * 0.5 else cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"

def build(s):
    c = s["case"]; eps = s["episode"]
    what = what_changed(s)
    stop = {"clear": "Probability at or below 0.15 on two or more independent pieces of legitimate-behaviour evidence (section 6); verification would not change the decision.",
            "decisive": "Probability at or above 0.85 on two or more independent fraud signals (section 6); further steps would not change the actions.",
            "denied": "The customer's denial settles the question (section 6); the graph evidence is consistent with it and further steps would not change the actions.",
            "scheme": "The shared element and the linked cards are identified and the analyst confirmed the scheme; further steps would not change the actions.",
            "r7": "The customer's confirmation of their own recurring charge settles the dispute (section 6).",
            "conflict": "Evidence conflicts; handed to an analyst under R8.",
            "verify": {"deny": "The customer's denial settled the question (section 6).",
                       "confirm": "The customer's confirmation settled the question (section 6).",
                       "no_reply": "No reply within 24 hours: acting under R4 and waiting on the customer; the graph offers no further evidence that would change the decision."}}
    sr = stop[s["mode"]] if s["mode"] != "verify" else stop["verify"][s["response"][0]]
    return {
        "case_id": c["case_id"],
        "case": {
            "status": s["status"], "verdict": s["verdict"], "fraud_probability": s["p1"], "pattern": s["pattern"] if s["verdict"] != "legitimate" else "none",
            "pattern_description": s["pattern_desc"] if s["pattern"] == "undocumented" and s["verdict"] == "fraud" else "",
            "affected_txn_ids": [str(x["id"]) for x in eps],
            "first_suspicious_txn_id": str(eps[0]["id"]) if eps else "",
            "connected_card_ids": s["connected"],
            "connected_device_profiles": s["devices"] if s["verdict"] != "legitimate" else [],
            "exposure_usd": s["exposure"],
            "evidence": evidence_items(s),
            "similar_prior_cases": [x for x in s["similar"] if str(x).startswith("CC-")],
            "summary": s["summary"],
            "written_to_graph": bool(s.get("written")) and s.get("backend") == "tigergraph-mcp",
            "graph_case_id": s["graph_case_id"] if s.get("written") and s.get("backend") == "tigergraph-mcp" else "",
        },
        "evidence_requests": s.get("requests", []),
        "next_best_actions": {"initial": s["initial"], "final": s["final"], "what_changed": what},
        "sar": {
            "file": s["sar_file"], "reason": s["sar_reason"], "narrative": s["sar_narrative"] if s["sar_file"] else "",
            "subjects": dataset_ids([c["customer_id"], c["card_id"]] + s["connected"]) if s["sar_file"] else [],
            "total_amount_usd": s["exposure"] if s["sar_file"] else 0,
            "activity_dates": [eps[0]["ts"].strftime("%Y-%m-%d"), eps[-1]["ts"].strftime("%Y-%m-%d")] if s["sar_file"] and eps else [],
        },
        "stop_reason": sr,
        "tool_calls": s["tool_calls"], "tokens": s["tokens"], "latency_s": s["latency_s"],
    }
