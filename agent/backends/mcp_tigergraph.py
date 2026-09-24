"""TigerGraph backend: every graph read/write goes through the TigerGraph MCP server
(tigergraph-mcp, stdio transport). The agent only ever calls MCP tools:
  tigergraph__run_installed_query   -> our GSQL investigation queries
  tigergraph__add_node / add_edges  -> writing the InvestigationCase (case memory)
  tigergraph__upsert_vectors        -> embedding the case for later semantic retrieval
"""
import asyncio, json, os, re, threading
import pandas as pd
from .base import GraphBackend

TXN_FIELDS = {"amount":"amount","product":"product","channel":"channel","risk_score":"risk_score","region":"region",
              "p_email":"p_email","r_email":"r_email","device_status":"device_new_flag","proxy":"proxy_type",
              "c1":"c1","c13":"c13","d1":"d1","dist1":"dist1"}

NUMERIC_SENTINEL = {"dist1", "d1", "c1", "c13", "risk_score"}   # loader stores missing numerics as -1

def _clean(v, key=None):
    if v in ("", None): return None
    if key in NUMERIC_SENTINEL and isinstance(v, (int, float)) and v < 0: return None
    return v

class _MCPClient:
    """Keeps one MCP stdio session alive on a background event loop; exposes a sync call()."""
    def __init__(self, env):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import get_default_environment, stdio_client
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self._ready = threading.Event()
        async def _run():
            import sys
            # launch the MCP server with this interpreter: works in any venv, on Windows too
            params = StdioServerParameters(command=sys.executable, args=["-c", "from tigergraph_mcp.main import main; main()"],
                                           env={**get_default_environment(), **env})
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    self.session = s
                    self._ready.set()
                    await asyncio.Event().wait()   # keep open
        self._task = asyncio.run_coroutine_threadsafe(_run(), self.loop)
        if not self._ready.wait(60):
            raise RuntimeError("tigergraph-mcp did not start; check TG_* settings in .env")

    def call(self, tool, args):
        fut = asyncio.run_coroutine_threadsafe(self.session.call_tool(tool, arguments=args), self.loop)
        res = fut.result(timeout=300)
        text = "".join(getattr(c, "text", "") for c in res.content)
        m = re.search(r"```json\n(.*?)\n```", text, re.S)
        payload = json.loads(m.group(1)) if m else {"success": False, "error": text}
        if not payload.get("success", False):
            raise RuntimeError(f"{tool} failed: {payload.get('error') or text[:400]}")
        return payload.get("data", {})

class MCPTigerGraphBackend(GraphBackend):
    name = "tigergraph-mcp"
    def __init__(self, env=None):
        env = env or {k: v for k, v in os.environ.items() if k.startswith("TG_")}
        self.graph = env.get("TG_GRAPHNAME", "FraudGraph")
        self.mcp = _MCPClient(env)

    # ---- plumbing ----
    def q(self, name, **params):
        data = self.mcp.call("tigergraph__run_installed_query", {"query_name": name, "params": params, "graph_name": self.graph})
        return data.get("result", [])

    @staticmethod
    def _pick(results, key):
        for block in results:
            if key in block: return block[key]
        return None

    @staticmethod
    def _txn(v, card_id=None):
        a = v.get("attributes", v)
        t = {"id": str(v.get("v_id", a.get("id"))), "ts": pd.Timestamp(a["ts"])}
        for k, src in TXN_FIELDS.items(): t[k] = _clean(a.get(src), k)
        dev = a.get("@device") or []; fp = a.get("@fingerprint") or []; card = a.get("@card") or []
        t["device_id"] = dev[0] if dev else None
        t["fingerprint"] = fp[0] if fp else None
        t["card_id"] = card_id or (card[0] if card else None)
        return t

    @staticmethod
    def _case(v):
        a = v.get("attributes", v)
        return {"id": v.get("v_id", a.get("id")), "outcome": a.get("outcome"), "pattern": a.get("pattern"),
                "card_id": a.get("card_id"), "opened_at": a.get("opened_at"), "exposure": a.get("exposure"),
                "report_filed": a.get("report_filed"), "notes": a.get("notes"), "txn_ids": []}

    @staticmethod
    def _ts(x): return pd.Timestamp(x).strftime("%Y-%m-%d %H:%M:%S")

    # ---- tools ----
    def txn_context(self, txn_id):
        r = self.q("txn_context", t=str(txn_id))
        t = self._txn(self._pick(r, "txn")[0])
        t["card_id"] = (self._pick(r, "card") or [None])[0]
        t["customer_id"] = (self._pick(r, "customer") or [None])[0]
        t["fingerprint"] = (self._pick(r, "fingerprint") or [None])[0]
        t["device_id"] = (self._pick(r, "device") or [None])[0]
        t["device_profile"] = (self._pick(r, "device_profile") or [None])[0]
        return t

    def card_profile(self, card_id, before_ts):
        r = self.q("card_profile", card=card_id, before_ts=self._ts(before_ts))
        m = {k: v for b in r for k, v in b.items()}
        return {"n": m["n"], "total": m["amount_total"], "sumsq": m["sumsq"], "max_amount": m["max_amount"],
                "regions": m["regions"], "products": m["products"], "region_days": m["region_days"], "devices": m["devices"]}

    def card_window(self, card_id, from_ts, to_ts):
        r = self.q("card_window", card=card_id, from_ts=self._ts(from_ts), to_ts=self._ts(to_ts))
        return sorted([self._txn(v, card_id) for v in self._pick(r, "T") or []], key=lambda t: t["ts"])

    def fingerprint_history(self, fp):
        r = self.q("fingerprint_history", fp=fp)
        card = fp.split("|")[0][2:]
        txns = sorted([self._txn(v, card) for v in self._pick(r, "T") or []], key=lambda t: t["ts"])
        for t in txns: t["fingerprint"] = fp
        return {"txns": txns, "closed_cases": [self._case(v) for v in self._pick(r, "C") or []],
                "agent_cases": [v.get("attributes", v) for v in self._pick(r, "I") or []]}

    def device_neighbors(self, device_id, from_ts, to_ts):
        r = self.q("device_neighbors", dev=device_id, from_ts=self._ts(from_ts), to_ts=self._ts(to_ts))
        win = sorted([self._txn(v) for v in self._pick(r, "WIN") or []], key=lambda t: t["ts"])
        for t in win: t["device_id"] = device_id
        return {"window": win, "closed_cases": [self._case(v) for v in self._pick(r, "CASES") or []],
                "lifetime_txns": self._pick(r, "lifetime_txns"), "lifetime_cards": self._pick(r, "lifetime_cards"), "profile": None}

    def amount_recurrence(self, card_id, amount, tol, product, before_ts):
        r = self.q("amount_recurrence", card=card_id, amount=amount, tol=tol, product=product, before_ts=self._ts(before_ts))
        return sorted([self._txn(v, card_id) for v in self._pick(r, "T") or []], key=lambda t: t["ts"])

    def card_cases(self, card_id):
        r = self.q("card_cases", card=card_id)
        return {"closed_cases": [self._case(v) for v in (self._pick(r, "C") or []) + (self._pick(r, "CC") or [])],
                "agent_cases": [v.get("attributes", v) for v in self._pick(r, "I") or []]}

    def threshold_scan(self, from_ts, to_ts, lo, hi):
        r = self.q("threshold_scan", from_ts=self._ts(from_ts), to_ts=self._ts(to_ts), lo=lo, hi=hi)
        out = []
        for v in self._pick(r, "B") or []:
            a = v["attributes"]
            out.append({"id": v["v_id"], "ts": pd.Timestamp(a["B.ts"]), "amount": a["B.amount"], "card_id": (a["B.@card"] or [None])[0]})
        return out

    def shared_device_scan(self, from_ts, to_ts, min_cards, max_lifetime_cards):
        r = self.q("shared_device_scan", from_ts=self._ts(from_ts), to_ts=self._ts(to_ts), min_cards=min_cards, max_lifetime_cards=max_lifetime_cards)
        out = []
        for v in self._pick(r, "D") or []:
            a = v["attributes"]
            out.append({"device_id": v["v_id"], "profile": a.get("D.profile"), "win_cards": sorted(a.get("D.@win_cards", [])),
                        "life_cards": a.get("D.@life_cards.size()"), "new_flags": a.get("D.@new_flags"), "proxy_flags": a.get("D.@proxy_flags")})
        return out

    def ring_component(self, device_id, max_list):
        r = self.q("ring_component", dev=device_id, max_list=max_list)
        return {"comp_id": self._pick(r, "comp_id"), "n_fingerprints": self._pick(r, "n_fingerprints") or 0,
                "n_devices": self._pick(r, "n_devices") or 0, "fingerprints": sorted(self._pick(r, "fingerprints") or []),
                "devices": sorted(self._pick(r, "devices") or [])}

    def wcc_ring_scan(self, min_devices, max_fingerprints):
        r = self.q("wcc_ring_scan", min_devices=min_devices, max_fingerprints=max_fingerprints)
        devs, fps = self._pick(r, "ring_devices") or {}, self._pick(r, "ring_fingerprints") or {}
        return [{"comp_id": int(c), "devices": sorted(devs[c]), "fingerprints": sorted(fps.get(c, []))} for c in sorted(devs, key=int)]

    def vector_search(self, kind, query_vec, k):
        query = {"knowledge": "knowledge_search", "cases": "similar_case_search"}[kind]
        r = self.q(query, query_vec=[round(float(x), 6) for x in query_vec], k=k)
        dist = self._pick(r, "distances") or {}
        out = []
        for v in self._pick(r, "v") or []:
            a = v.get("attributes", {})
            d = dist.get(v["v_id"], dist.get(str(v["v_id"]), 1.0))
            out.append({"id": v["v_id"], "score": round(1 - float(d), 4), **{kk: vv for kk, vv in a.items() if kk != "embedding"}})
        return out

    def write_case(self, case_vertex, edges, embedding=None):
        """Case memory write-back, entirely through MCP tools: the vertex, its edges, its embedding."""
        cid = case_vertex["id"]
        attrs = {k: v for k, v in case_vertex.items() if k not in ("id", "record")}
        self.mcp.call("tigergraph__add_node", {"vertex_type": "InvestigationCase", "vertex_id": cid, "attributes": attrs, "graph_name": self.graph})
        groups = [("ON_CARD", "Card", [edges["card_id"]]), ("INVOLVES", "Txn", edges.get("txn_ids") or []),
                  ("CONNECTED_TO", "Card", edges.get("connected_cards") or []), ("CASE_DEVICE", "DeviceProfile", edges.get("device_ids") or []),
                  ("HAS_PATTERN", "FraudPattern", [edges["pattern"]]),
                  ("SIMILAR_TO", "ClosedCase", [x for x in edges.get("similar_cases") or [] if str(x).startswith("CC-")]),
                  ("SIMILAR_TO", "InvestigationCase", [x for x in edges.get("similar_cases") or [] if str(x).startswith("INV-")])]
        for etype, ttype, targets in groups:
            if not targets: continue
            self.mcp.call("tigergraph__add_edges", {"edge_type": etype, "graph_name": self.graph,
                          "edges": [{"source_type": "InvestigationCase", "source_id": cid, "target_type": ttype, "target_id": str(t)} for t in targets]})
        if embedding is not None:
            self.mcp.call("tigergraph__upsert_vectors", {"vertex_type": "InvestigationCase", "vector_attribute": "embedding",
                          "vectors": [{"vertex_id": cid, "vector": [round(float(x), 6) for x in embedding]}], "graph_name": self.graph})
        return {"written": cid, "edges": sum(len(g[2]) for g in groups)}
