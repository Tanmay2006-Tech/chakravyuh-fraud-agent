"""Proves the graph-algorithm evidence (GDS tg_wcc, weight 0) is context only: with and without it, every
case reaches the same probability, verdict, pattern, episode, actions, report and retrieved prior cases.
Offline, no server or LLM needed.   python -m tests.test_graph_algorithm"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent import assess, graph_algorithm
from agent.detectors import Signal
from agent.runtime import _local, load_case_pack
from agent.knowledge import Embedder
from agent.toolbox import ToolBox
from agent.llm import LLM
from agent.workflow import Investigator
from agent.answer import build as build_answer

def decisions(ans):
    c, n = ans["case"], ans["next_best_actions"]
    return (c["fraud_probability"], c["verdict"], c["pattern"], c["affected_txn_ids"], c["exposure_usd"], c["connected_card_ids"],
            c["similar_prior_cases"], c["summary"], [(a["action"], a["route"]) for a in n["initial"]], [(a["action"], a["route"]) for a in n["final"]],
            ans["sar"]["file"], ans["sar"]["narrative"], [e for e in c["evidence"] if not e["ref"].startswith("query:ring_component")])

if __name__ == "__main__":
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY"): os.environ.pop(k, None)
    bad = 0
    # 1. arithmetic: a weight-0 signal changes neither the probability nor the independent-evidence counts
    base = [Signal("structuring", 3.0, "x", "r", []), Signal("device_memory", -0.7, "y", "r", [])]
    zero = base + [Signal("graph_algorithm", 0.0, "z", "r", [])]
    for trig in ("risk_score", "customer_report", "analyst_request"):
        same = (assess.probability(trig, base), assess.independent(base, 1), assess.independent(base, -1)) == \
               (assess.probability(trig, zero), assess.independent(zero, 1), assess.independent(zero, -1))
        bad += not same
    # 2. end to end: all 20 cases with the tg_wcc node and with it switched off
    emb = Embedder("hashed"); local = _local(emb); local.agent_cases_path = os.path.join(os.path.dirname(__file__), ".test_mem.jsonl")
    cases = load_case_pack()
    with_ga = {c["case_id"]: build_answer(Investigator(ToolBox(local, emb), LLM()).run(c)) for c in cases}
    real = graph_algorithm.component_signal
    graph_algorithm.component_signal = lambda tb, ctx: None
    try:
        without = {c["case_id"]: build_answer(Investigator(ToolBox(local, emb), LLM()).run(c)) for c in cases}
    finally:
        graph_algorithm.component_signal = real
    shown = 0
    for cid in with_ga:
        ga = [e for e in with_ga[cid]["case"]["evidence"] if e["ref"].startswith("query:ring_component")]
        shown += bool(ga)
        if decisions(with_ga[cid]) != decisions(without[cid]): bad += 1; print("DIFF", cid)
        if ga: print(f"{cid}: {ga[0]['claim'][:150]}...")
    if os.path.exists(local.agent_cases_path): os.remove(local.agent_cases_path)
    print(f"graph_algorithm evidence shown in {shown} of 20 cases; decisions identical with and without it: {bad == 0}")
    print("RESULT:", "PASS" if bad == 0 and shown else "FAIL")
