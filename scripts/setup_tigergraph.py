"""One-shot TigerGraph setup, done entirely through the TigerGraph MCP server:
   schema -> vector attributes -> loading jobs -> data -> knowledge docs -> embeddings -> install queries
   -> graph algorithm (USES_DEVICE projection + GDS tg_wcc).
   Usage:  python scripts/setup_tigergraph.py            (all steps)
           python scripts/setup_tigergraph.py --from load   (resume from a step)
"""
import argparse, glob, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from agent.runtime import load_env
from agent.knowledge import Embedder, all_docs, PATTERNS

STEPS = ["schema", "vectors", "jobs", "load", "knowledge", "embed", "queries", "algorithms"]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--from", dest="start", default="schema", choices=STEPS)
    ap.add_argument("--embedder", default="fastembed")
    a = ap.parse_args(); load_env()
    from agent.backends.mcp_tigergraph import _MCPClient
    env = {k: v for k, v in os.environ.items() if k.startswith("TG_")}
    G = env.get("TG_GRAPHNAME", "FraudGraph")
    mcp = _MCPClient(env)
    def call(tool, args, ok_if=("already", "exist", "used by")):
        try:
            r = mcp.call(tool, args); return r
        except Exception as e:
            if any(s in str(e).lower() for s in ok_if): print("   (skipped:", str(e)[:120], ")"); return None
            raise
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    todo = STEPS[STEPS.index(a.start):]
    t0 = time.time()

    if "schema" in todo:
        print("1/8 schema (one statement at a time)")
        stmts = [l.strip() for l in open(f"{root}/tigergraph/schema.gsql").read().splitlines() if l.strip() and not l.strip().startswith("#")]
        for st in stmts:
            label = " ".join(st.split()[:3])
            try:
                mcp.call("tigergraph__gsql", {"command": st})
                print(f"    ok   {label}")
            except Exception as e:
                msg = str(e)
                if any(w in msg.lower() for w in ("already", "exist", "used by")):
                    print(f"    -    {label} (already exists)")
                else:
                    print(f"    FAIL {label}\n      {msg[:500]}")
                    print("\nStopped at this statement.")
                    sys.exit(1)
    if "vectors" in todo:
        print("2/8 vector attributes (TigerVector, 384-d cosine)")
        for vt in ("ClosedCase", "KnowledgeDoc", "InvestigationCase"):
            call("tigergraph__add_vector_attribute", {"graph_name": G, "vertex_type": vt, "vector_name": "embedding", "dimension": 384, "metric": "COSINE"})
    if "jobs" in todo:
        print("3/8 loading jobs"); call("tigergraph__gsql", {"command": open(f"{root}/tigergraph/loading_jobs.gsql").read()})
    if "load" in todo:
        print("4/8 loading data (a few minutes)")
        L = os.path.abspath(f"{root}/data/load")
        for path, tag, job in [(f"{L}/customers.csv", "customers", "load_entities"), (f"{L}/cards.csv", "cards", "load_entities"),
                               (f"{L}/devices.csv", "devices", "load_entities")] + \
                              [(p, "f", "load_txns") for p in sorted(glob.glob(f"{L}/transactions_*.csv"))] + \
                              [(f"{L}/closed_cases.csv", "cases", "load_cases"), (f"{L}/case_involves.csv", "involves", "load_cases"),
                               (f"{L}/case_connected.csv", "connected", "load_cases")]:
            print("   ", os.path.basename(path))
            call("tigergraph__run_loading_job_with_file", {"graph_name": G, "file_path": path, "file_tag": tag, "job_name": job, "timeout": 600000})
    if "knowledge" in todo:
        print("5/8 knowledge docs + pattern vertices")
        for did, kind, title, content in all_docs():
            call("tigergraph__add_node", {"graph_name": G, "vertex_type": "KnowledgeDoc", "vertex_id": did, "attributes": {"kind": kind, "title": title, "content": content}})
        for p, d in list(PATTERNS.items()) + [("undocumented", "Confirmed fraud matching none of the documented patterns."), ("none", "Cleared: not fraud.")]:
            call("tigergraph__add_node", {"graph_name": G, "vertex_type": "FraudPattern", "vertex_id": p, "attributes": {"description": d}})
    if "embed" in todo:
        print("6/8 embeddings (closed-case narratives + knowledge)")
        os.makedirs(f"{root}/data/vectors", exist_ok=True)
        if os.path.exists(f"{root}/data/vectors/embedder.txt"): os.remove(f"{root}/data/vectors/embedder.txt")
        emb = Embedder(a.embedder); print("    embedder:", emb.kind)
        open(f"{root}/data/vectors/embedder.txt", "w").write(emb.kind)
        cc = pd.read_parquet(f"{root}/data/local/closed_cases.parquet")
        docs = all_docs()
        for name, ids, texts, vt in [("closed_cases", cc.case_id.tolist(), cc.analyst_notes.fillna("").tolist(), "ClosedCase"),
                                     ("knowledge", [d[0] for d in docs], [d[3] for d in docs], "KnowledgeDoc")]:
            V = emb.embed(texts)
            path = os.path.abspath(f"{root}/data/vectors/{name}.csv")
            with open(path, "w") as f:
                for i, v in zip(ids, V): f.write(f"{i}|{','.join(f'{x:.6f}' for x in v)}\n")
            call("tigergraph__load_vectors_from_csv", {"graph_name": G, "vertex_type": vt, "vector_attribute": "embedding", "file_path": path})
    if "queries" in todo:
        print("7/8 creating + installing GSQL queries (install compiles everything once; a few minutes)")
        bad = []
        for q in sorted(glob.glob(f"{root}/tigergraph/queries/*.gsql")):
            name = os.path.basename(q)[:-5]
            try:
                out = mcp.call("tigergraph__gsql", {"command": f"USE GRAPH {G}\n" + open(q).read()})
                txt = str(out).lower()
                # success is stated explicitly; a warning such as WARN-5 ("compare with an error margin") is not a failure
                if "successfully created" in txt and "semantic check fails" not in txt:
                    print(f"    ok   {name}" + ("  (compiled with warnings)" if "warning" in txt else ""))
                else:
                    bad.append(name); print(f"    FAIL {name}: {str(out)[:300]}")
            except Exception as e:
                bad.append(name); print(f"    FAIL {name}: {str(e)[:300]}")
        out = call("tigergraph__gsql", {"command": f"USE GRAPH {G}\nINSTALL QUERY ALL"})
        print("    install:", str(out)[-300:])
        if bad: print("    queries that did not compile (the agent falls back to the offline mirror for these):", ", ".join(bad))
    if "algorithms" in todo:
        # GDS graph algorithm: project the card-in-use x rare-device graph (USES_DEVICE), then run the unmodified
        # tg_wcc from github.com/tigergraph/gsql-graph-algorithms, which writes each vertex's component to wcc_id.
        from agent.backends.base import WCC_FROM, WCC_TO, WCC_MAX_LIFETIME_CARDS
        print("8/8 graph algorithm: USES_DEVICE projection + GDS tg_wcc")
        names = []
        for q in sorted(glob.glob(f"{root}/tigergraph/algorithms/*.gsql")):
            name = os.path.basename(q)[:-5]; names.append(name)
            try:
                out = str(mcp.call("tigergraph__gsql", {"command": f"USE GRAPH {G}\n" + open(q).read()})).lower()
            except Exception as e:     # the GDS file is CREATE QUERY (not OR REPLACE): on a re-run it already exists
                out = str(e).lower()
            state = "ok  " if "successfully created" in out else "-   " if "already exists" in out or "used by another object" in out else "FAIL"
            print(f"    {state} {name}" + (" (already exists)" if state == "-   " else "" if state == "ok  " else f": {out[:300]}"))
        out = call("tigergraph__gsql", {"command": f"USE GRAPH {G}\nINSTALL QUERY {', '.join(names)}"})
        print("    install:", str(out)[-160:])
        r = mcp.call("tigergraph__run_installed_query", {"graph_name": G, "query_name": "build_uses_device",
                     "params": {"from_ts": WCC_FROM, "to_ts": WCC_TO, "max_lifetime_cards": WCC_MAX_LIFETIME_CARDS}})
        print("    build_uses_device:", r.get("result"))
        mcp.call("tigergraph__run_installed_query", {"graph_name": G, "query_name": "tg_wcc",
                 "params": {"v_type_set": ["Fingerprint", "DeviceProfile"], "e_type_set": ["USES_DEVICE"],
                            "print_results": False, "result_attribute": "wcc_id"}})
        print("    tg_wcc: components written to wcc_id")
    print(f"done in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
