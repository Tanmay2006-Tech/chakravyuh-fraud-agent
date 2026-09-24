"""Run the agent on the 20 benchmark cases and write cases/<case_id>.json (+ traces for the UI).
   python run_cases.py --backend tigergraph        # live: TigerGraph through MCP
   python run_cases.py --backend local             # offline mirror
"""
import argparse, json, os, time
from agent.runtime import build, load_case_pack
from agent.answer import build as build_answer

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="tigergraph", choices=["tigergraph", "local"])
    ap.add_argument("--embedder", default="fastembed", choices=["fastembed", "hashed"])
    ap.add_argument("--cases", default="")
    ap.add_argument("--out", default="cases")
    ap.add_argument("--pause", type=float, default=5, help="seconds to sleep between cases (stays under free-tier LLM per-minute limits)")
    ap.add_argument("--strict", action="store_true", help="fail instead of using the offline fallback for a failed TigerGraph call")
    a = ap.parse_args()
    inv = build(a.backend, a.embedder, strict=a.strict)
    os.makedirs(a.out, exist_ok=True); os.makedirs("traces", exist_ok=True)
    pack = load_case_pack()
    if a.cases: pack = [c for c in pack if c["case_id"] in a.cases.split(",")]
    for i, c in enumerate(pack):
        if i and a.pause: time.sleep(a.pause)
        s = inv.run(c)
        ans = build_answer(s)
        json.dump(ans, open(f"{a.out}/{c['case_id']}.json", "w"), indent=2, default=str)
        served = s["tool_calls"] - s.get("fallback_calls", 0) if s.get("engine") == "tigergraph-mcp" else 0
        json.dump({"case_id": c["case_id"], "backend": a.backend, "writer": s["writer"], "p0": s["p0"], "tigergraph_calls": served, "branches": s.get("branches", []), "timeline": s.get("timeline", []), "executions": s.get("executions", []), "trace": s["trace"]},
                  open(f"traces/{c['case_id']}.json", "w"), indent=2, default=str)
        k = ans["case"]
        print(f"{c['case_id']}  {k['verdict']:<10} p={k['fraud_probability']:.2f}  {k['pattern']:<28} exp=${k['exposure_usd']:>9,.2f}  "
              f"SAR={ans['sar']['file']!s:<5}  TG={served}/{ans['tool_calls']}  final={[x['action'] for x in ans['next_best_actions']['final']]}")
    print(f"LLM: {inv.llm.retries} same-provider retries, {inv.llm.failovers} failovers")

if __name__ == "__main__":
    main()
