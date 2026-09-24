"""Wires backend + embedder + LLM + investigator."""
import os
import numpy as np
import pandas as pd
from .knowledge import Embedder, all_docs
from .toolbox import ToolBox
from .llm import LLM
from .workflow import Investigator

def load_env():
    try:
        from dotenv import load_dotenv; load_dotenv()
    except ImportError:
        pass

def _local(emb):
    from .backends.local import LocalBackend
    b = LocalBackend(os.getenv("LOCAL_DATA", "data/local"))
    docs = all_docs()
    b.upsert_vectors("knowledge", [d[0] for d in docs], emb.embed([d[3] for d in docs]),
                     [{"kind": d[1], "title": d[2], "content": d[3]} for d in docs])
    cc = b.cases
    cache = os.path.join(os.getenv("LOCAL_DATA", "data/local"), f"case_vectors_{emb.kind}.npy")
    if os.path.exists(cache):
        V = np.load(cache)
    else:
        V = emb.embed(cc.analyst_notes.fillna("").tolist()); np.save(cache, V)
    b.upsert_vectors("cases", cc.case_id.tolist(), V, [{"pattern": p, "outcome": o} for p, o in zip(cc.pattern, cc.outcome)])
    return b

def build(kind="local", embedder="fastembed", strict=False):
    """kind: 'tigergraph' (MCP, with per-call offline fallback unless strict) or 'local'."""
    load_env()
    emb = Embedder(embedder)
    if kind == "tigergraph":
        from .backends.mcp_tigergraph import MCPTigerGraphBackend
        from .backends.hybrid import HybridBackend
        backend = HybridBackend(MCPTigerGraphBackend(), _local(emb), strict=strict)
    else:
        backend = _local(emb)
    return Investigator(ToolBox(backend, emb), LLM())

def load_case_pack(path="data/local/case_pack.csv"):
    cp = pd.read_csv(path)
    return [{**r, "flagged_txn_id": str(r["flagged_txn_id"]), "risk_score": None if pd.isna(r["risk_score"]) else r["risk_score"]}
            for r in cp.to_dict("records")]
