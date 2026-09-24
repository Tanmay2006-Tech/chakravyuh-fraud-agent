"""Reset the agent's own case memory in TigerGraph before re-running the cases.

run_cases.py and monitor.py write InvestigationCase vertices (INV-HHG-001..020, INV-MON-001..014) and the
agent recalls them as memory. Upserts prevent duplicates, but a second run would find the *previous* run's
investigations, including monitoring cases that were derived from these very cases, and cite them as prior
evidence. Reset first, then run the cases, then the monitor:

    python scripts/reset_case_memory.py            # count only (dry run)
    python scripts/reset_case_memory.py --delete   # count, delete those vertices (and their edges), confirm 0
    python run_cases.py --backend tigergraph
    python monitor.py --backend tigergraph

Only the listed IDs are touched; the loaded data, closed cases and knowledge documents are never deleted.
"""
import argparse, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.runtime import load_env

IDS = [f"INV-HHG-{i:03d}" for i in range(1, 21)] + [f"INV-MON-{i:03d}" for i in range(1, 15)]

def run(mcp, graph, delete):
    ids = ", ".join(f'"{x}"' for x in IDS)
    body = (f"SetAccum<STRING> @@ids = ({ids}); A = {{InvestigationCase.*}}; "
            "T = SELECT s FROM A:s WHERE @@ids.contains(s.id); "
            "PRINT A.size() AS all_investigation_cases, T.size() AS targeted; " + ("DELETE s FROM T:s; " if delete else ""))
    out = mcp.call("tigergraph__gsql", {"command": f"USE GRAPH {graph}\nINTERPRET QUERY () FOR GRAPH {graph} {{ {body} }}"})
    txt = str(out).replace("\\n", "\n").replace('\\"', '"')
    if '"error": true' in txt: raise RuntimeError("GSQL error: " + txt[:400])
    got = {k: int(v) for k, v in re.findall(r'"(all_investigation_cases|targeted)": (\d+)', txt)}
    if len(got) != 2: raise RuntimeError("unexpected response: " + txt[:400])
    return got

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--delete", action="store_true", help="delete the targeted vertices (default: count only)")
    a = ap.parse_args(); load_env()
    from agent.backends.mcp_tigergraph import _MCPClient
    env = {k: v for k, v in os.environ.items() if k.startswith("TG_")}
    mcp, graph = _MCPClient(env), env.get("TG_GRAPHNAME", "FraudGraph")
    before = run(mcp, graph, delete=False)
    print(f"InvestigationCase vertices: {before['all_investigation_cases']} in the graph, {before['targeted']} of them are INV-HHG-001..020 / INV-MON-001..014")
    if not a.delete:
        print("dry run: nothing deleted (add --delete)"); return
    run(mcp, graph, delete=True)
    after = run(mcp, graph, delete=False)
    print(f"deleted; now {after['targeted']} targeted vertices remain ({after['all_investigation_cases']} InvestigationCase vertices in total)")
    if after["targeted"]: sys.exit("reset incomplete")

if __name__ == "__main__":
    main()
