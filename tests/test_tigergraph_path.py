"""End-to-end test of the TigerGraph code path without a server.
A fake MCP layer answers each installed query in the exact JSON shape TigerGraph returns
(PRINT of vertex sets / accumulators / projections), built from the offline data. The whole
investigation then runs through MCPTigerGraphBackend and must reach the same decisions as the
offline backend.   python -m tests.test_tigergraph_path"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.runtime import _local, load_case_pack
from agent.knowledge import Embedder
from agent.toolbox import ToolBox
from agent.llm import LLM
from agent.workflow import Investigator
from agent.answer import build as build_answer
from agent.backends.mcp_tigergraph import MCPTigerGraphBackend

def V(t, extra=None):
    a = {"id": t["id"], "ts": t["ts"].strftime("%Y-%m-%d %H:%M:%S"), "amount": t["amount"], "product": t["product"], "channel": t["channel"],
         "risk_score": t["risk_score"] if t["risk_score"] is not None else -1, "region": t["region"], "country": "",
         "dist1": t["dist1"] if t["dist1"] is not None else -1, "p_email": t["p_email"] or "", "r_email": t["r_email"] or "",
         "c1": t["c1"] if t["c1"] is not None else -1, "c13": t["c13"] if t["c13"] is not None else -1, "d1": t["d1"] if t["d1"] is not None else -1,
         "m4": "", "device_new_flag": t["device_status"] or "", "proxy_type": t["proxy"] or ""}
    a.update(extra or {})
    return {"v_id": t["id"], "v_type": "Txn", "attributes": a}

def C(c):
    return {"v_id": c["id"], "v_type": "ClosedCase", "attributes": {"id": c["id"], "outcome": c["outcome"], "pattern": c["pattern"], "card_id": c["card_id"],
            "opened_at": c["opened_at"], "exposure": c["exposure"], "report_filed": c["report_filed"], "notes": c["notes"]}}

class FakeTG(MCPTigerGraphBackend):
    def __init__(self, local):
        self.L, self.graph, self.writes = local, "FraudGraph", []
        class M:
            def call(_, tool, args): self.writes.append((tool, args)); return {}
        self.mcp = M()
    def q(self, name, **p):
        L = self.L
        if name == "txn_context":
            t = L.txn_context(p["t"])
            return [{"txn": [V(t)], "card": [t["card_id"]], "customer": [t["customer_id"]], "fingerprint": [t["fingerprint"]],
                     "device": [t["device_id"]] if t["device_id"] else [], "device_profile": [t["device_profile"]] if t["device_profile"] else []}]
        if name == "card_profile":
            r = L.card_profile(p["card"], p["before_ts"]); r = {**r, "amount_total": r.pop("total")}; return [r]
        if name == "card_window":
            return [{"T": [V(t, {"@device": [t["device_id"]] if t["device_id"] else [], "@fingerprint": [t["fingerprint"]]}) for t in L.card_window(p["card"], p["from_ts"], p["to_ts"])]}]
        if name == "fingerprint_history":
            r = L.fingerprint_history(p["fp"])
            return [{"T": [V(t) for t in r["txns"]]}, {"C": [C(c) for c in r["closed_cases"]]}, {"I": []}]
        if name == "device_neighbors":
            r = L.device_neighbors(p["dev"], p["from_ts"], p["to_ts"])
            return [{"WIN": [V(t, {"@card": [t["card_id"]]}) for t in r["window"]]}, {"CASES": [C(c) for c in r["closed_cases"]]},
                    {"lifetime_txns": r["lifetime_txns"], "lifetime_cards": r["lifetime_cards"]}]
        if name == "amount_recurrence":
            return [{"T": [V(t, {"@fingerprint": [t["fingerprint"]]}) for t in L.amount_recurrence(p["card"], p["amount"], p["tol"], p["product"], p["before_ts"])]}]
        if name == "card_cases":
            r = L.card_cases(p["card"]); return [{"C": [C(c) for c in r["closed_cases"]]}, {"CC": []}, {"I": []}]
        if name == "threshold_scan":
            return [{"B": [{"v_id": x["id"], "v_type": "Txn", "attributes": {"B.ts": x["ts"].strftime("%Y-%m-%d %H:%M:%S"), "B.amount": x["amount"], "B.@card": [x["card_id"]]}}
                           for x in L.threshold_scan(p["from_ts"], p["to_ts"], p["lo"], p["hi"])]}]
        if name == "ring_component":
            r = L.ring_component(p["dev"], p["max_list"])
            return [{"comp_id": r["comp_id"], "n_fingerprints": r["n_fingerprints"], "n_devices": r["n_devices"],
                     "fingerprints": r["fingerprints"], "devices": r["devices"]}]
        if name == "wcc_ring_scan":
            rings = L.wcc_ring_scan(p["min_devices"], p["max_fingerprints"])
            return [{"ring_devices": {str(x["comp_id"]): x["devices"] for x in rings},
                     "ring_fingerprints": {str(x["comp_id"]): x["fingerprints"] for x in rings}}]
        if name in ("knowledge_search", "similar_case_search"):
            r = L.vector_search("knowledge" if name == "knowledge_search" else "cases", p["query_vec"], p["k"])
            return [{"v": [{"v_id": x["id"], "v_type": "X", "attributes": {k: v for k, v in x.items() if k not in ("id", "score")}} for x in r]},
                    {"distances": {x["id"]: 1 - x["score"] for x in r}}]
        raise KeyError(name)

def run(backend, emb, cases):
    inv = Investigator(ToolBox(backend, emb), LLM())
    return {c["case_id"]: build_answer(inv.run(c)) for c in cases}

if __name__ == "__main__":
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY"): os.environ.pop(k, None)
    emb = Embedder("hashed"); local = _local(emb); local.agent_cases_path = "/tmp/chakravyuh_test_mem.jsonl"
    cases = load_case_pack()
    a = run(local, emb, cases)
    fake = FakeTG(local)
    b = run(fake, emb, cases)
    bad = 0
    for cid in a:
        x, y = a[cid], b[cid]
        same = (x["case"]["verdict"], x["case"]["pattern"], x["case"]["affected_txn_ids"], x["case"]["exposure_usd"], x["sar"]["file"],
                [z["action"] for z in x["next_best_actions"]["final"]]) == \
               (y["case"]["verdict"], y["case"]["pattern"], y["case"]["affected_txn_ids"], y["case"]["exposure_usd"], y["sar"]["file"],
                [z["action"] for z in y["next_best_actions"]["final"]])
        bad += not same
        print(("OK  " if same else "DIFF"), cid, y["case"]["verdict"], y["case"]["written_to_graph"])
    # graph algorithm: the same tg_wcc evidence through MCP as offline, always with weight 0
    ga = lambda ans: [(e["claim"], e["entity_ids"]) for e in ans["case"]["evidence"] if e["ref"].startswith("query:ring_component")]
    for cid in a:
        if ga(a[cid]) != ga(b[cid]): bad += 1; print("DIFF graph_algorithm evidence", cid)
    n_ga = sum(bool(ga(b[c])) for c in b)
    scan = fake.wcc_ring_scan(2, 200)
    print(f"graph_algorithm evidence in {n_ga} cases; wcc_ring_scan found {len(scan)} multi-device rings")
    if n_ga == 0 or not scan: bad += 1; print("FAIL: graph algorithm path not exercised")
    tools = sorted({t for t, _ in fake.writes})
    print("MCP write tools used:", tools, "| writes:", len(fake.writes))
    print("RESULT:", "PASS" if bad == 0 else f"FAIL ({bad} differ)")
