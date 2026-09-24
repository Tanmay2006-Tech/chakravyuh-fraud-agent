"""Runs every installed query once through MCP with real IDs from the case pack and reports
OK / FAIL. Paste the output if anything fails."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from agent.runtime import load_env
from agent.knowledge import Embedder
load_env()
from agent.backends.mcp_tigergraph import MCPTigerGraphBackend
b = MCPTigerGraphBackend()
emb = Embedder(os.getenv("EMBEDDER", "fastembed"))
ts = pd.Timestamp("2016-12-03 19:36:30")
tests = [
    ("txn_context", lambda: b.txn_context("3523199")),
    ("card_profile", lambda: b.card_profile("C12265-K2", ts)),
    ("card_window", lambda: b.card_window("C12265-K2", ts - pd.Timedelta(hours=3), ts + pd.Timedelta(hours=1))),
    ("fingerprint_history", lambda: b.fingerprint_history(b.txn_context("3514948")["fingerprint"])),
    ("device_neighbors", lambda: b.device_neighbors(b.txn_context("3534820")["device_id"], ts - pd.Timedelta(days=15), ts + pd.Timedelta(days=15))),
    ("amount_recurrence", lambda: b.amount_recurrence("C08299-K1", 30.02, 0.15, "S", pd.Timestamp("2016-12-28"))),
    ("card_cases", lambda: b.card_cases("C11923-K2")),
    ("threshold_scan", lambda: b.threshold_scan(pd.Timestamp("2016-11-15"), pd.Timestamp("2016-11-30"), 400.0, 500.0)),
    ("shared_device_scan", lambda: b.shared_device_scan(pd.Timestamp("2016-11-20"), pd.Timestamp("2016-12-05"), 4, 80)),
    ("ring_component", lambda: b.ring_component(b.txn_context("3478561")["device_id"], 400)),   # HHG-014's device, GDS tg_wcc result
    ("wcc_ring_scan", lambda: b.wcc_ring_scan(2, 200)),
    ("knowledge_search", lambda: b.vector_search("knowledge", emb.embed(["customer denies the transaction block card"])[0], 3)),
    ("similar_case_search", lambda: b.vector_search("cases", emb.embed(["cardholder reported unrecognised online purchase from new device"])[0], 3)),
]
ok = 0
for name, fn in tests:
    t0 = time.time()
    try:
        r = fn()
        n = len(r) if isinstance(r, list) else ", ".join(f"{k}={len(v) if isinstance(v, (list, dict)) else v}" for k, v in list(r.items())[:4])
        print(f"OK    {name:<22} {time.time()-t0:5.1f}s  {n}"); ok += 1
    except Exception as e:
        print(f"FAIL  {name:<22} {str(e)[:400]}")
for name in ("tg_wcc", "build_uses_device"):     # graph-algorithm setup queries: installed, not re-run here
    try:
        inst = b.mcp.call("tigergraph__is_query_installed", {"graph_name": b.graph, "query_name": name}).get("installed")
        print(f"{'OK  ' if inst else 'FAIL'}  {name:<22}   installed (run by setup_tigergraph.py --from algorithms)"); ok += bool(inst)
    except Exception as e:
        print(f"FAIL  {name:<22} {str(e)[:300]}")
print(f"\n{ok}/{len(tests) + 2} queries working through MCP")
