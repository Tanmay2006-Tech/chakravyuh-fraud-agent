"""Offline mirror of the TigerGraph tool layer (pandas). Same contracts as the GSQL queries,
used for development, unit tests and for running without a live workspace."""
import json, os
import numpy as np, pandas as pd
from .base import GraphBackend, WCC_FROM, WCC_TO, WCC_MAX_LIFETIME_CARDS

def _f(x):
    return None if pd.isna(x) else x

class LocalBackend(GraphBackend):
    name = "local"
    def __init__(self, data_dir="data/local"):
        tx = pd.read_parquet(f"{data_dir}/transactions.parquet")
        tx = tx.rename(columns={"TransactionID":"id","TransactionAmt":"amount","ProductCD":"product",
                                "P_emaildomain":"p_email","R_emaildomain":"r_email","id_15":"device_status",
                                "id_23":"proxy","C1":"c1","C13":"c13","D1":"d1"})
        tx["id"] = tx["id"].astype(str)
        self.tx = tx.set_index("id", drop=False)
        tx = self.tx
        self.by_card = {k: v.index for k, v in tx.groupby("card_id")}
        self.by_fp = {k: v.index for k, v in tx.groupby("fingerprint")}
        self.by_dev = {k: v.index for k, v in tx.dropna(subset=["device_id"]).groupby("device_id")}
        self.dev_profile = tx.dropna(subset=["device_id"]).drop_duplicates("device_id").set_index("device_id").device_profile.to_dict()
        cc = pd.read_parquet(f"{data_dir}/closed_cases.parquet")
        self.cases = cc.set_index("case_id", drop=False)
        inv = cc.assign(t=cc.txn_ids.astype(str).str.split("|")).explode("t")
        inv["t"] = inv.t.astype(float).astype(int).astype(str)
        self.case_by_txn = inv.groupby("t").case_id.apply(list).to_dict()
        self.case_by_card = cc.groupby("card_id").case_id.apply(list).to_dict()
        self.agent_cases_path = os.path.join(data_dir, "agent_cases.jsonl")
        self.vectors = {}   # kind -> (ids, matrix, payloads)
        self.as_of = None   # backtests: hide closed cases opened at/after this time (no look-ahead)

    # ---------- helpers ----------
    def _rows(self, idx):
        cols = ["id","ts","amount","product","channel","risk_score","region","p_email","r_email","device_status",
                "proxy","device_id","fingerprint","card_id","c1","c13","d1","dist1"]
        df = self.tx.loc[idx, cols].sort_values("ts")
        return [{k: _f(v) for k, v in r.items()} for r in df.to_dict("records")]

    def _visible(self, cids):
        if self.as_of is None: return cids
        return [c for c in cids if pd.Timestamp(self.cases.loc[c, "opened_at"]) < self.as_of]

    def _case(self, cid):
        r = self.cases.loc[cid]
        return {"id": cid, "outcome": r.outcome, "pattern": r.pattern, "card_id": r.card_id, "opened_at": r.opened_at,
                "exposure": float(r.exposure_usd), "report_filed": r.report_filed, "notes": r.analyst_notes,
                "txn_ids": str(r.txn_ids).split("|")}

    # ---------- tools ----------
    def txn_context(self, txn_id):
        t = self._rows([str(txn_id)])[0]
        t["customer_id"] = t["card_id"].split("-")[0]
        t["device_profile"] = self.dev_profile.get(t["device_id"]) if t["device_id"] else None
        return t

    def card_profile(self, card_id, before_ts):
        d = self.tx.loc[self.by_card.get(card_id, [])]
        d = d[d.ts < pd.Timestamp(before_ts)]
        days = d.assign(day=d.ts.dt.floor("D")).groupby("region").day.nunique()
        return {"n": int(len(d)), "total": float(d.amount.sum()), "sumsq": float((d.amount**2).sum()),
                "max_amount": float(d.amount.max()) if len(d) else 0.0,
                "regions": d.region.value_counts().to_dict(), "products": d["product"].value_counts().to_dict(),
                "region_days": days.to_dict(), "devices": sorted(d.device_id.dropna().unique().tolist())}

    def card_window(self, card_id, from_ts, to_ts):
        d = self.tx.loc[self.by_card.get(card_id, [])]
        d = d[(d.ts >= pd.Timestamp(from_ts)) & (d.ts <= pd.Timestamp(to_ts))]
        return self._rows(d.index)

    def fingerprint_history(self, fp):
        idx = self.by_fp.get(fp, [])
        cids = self._visible(sorted({c for t in idx for c in self.case_by_txn.get(t, [])}))
        return {"txns": self._rows(idx), "closed_cases": [self._case(c) for c in cids],
                "agent_cases": [c for c in self._agent_cases() if set(c.get("txn_ids") or []) & set(idx)]}

    def device_neighbors(self, device_id, from_ts, to_ts):
        idx = self.by_dev.get(device_id, [])
        d = self.tx.loc[idx]
        w = d[(d.ts >= pd.Timestamp(from_ts)) & (d.ts <= pd.Timestamp(to_ts))]
        cids = self._visible(sorted({c for t in idx for c in self.case_by_txn.get(t, [])}))
        return {"window": self._rows(w.index), "closed_cases": [self._case(c) for c in cids],
                "lifetime_txns": int(len(d)), "lifetime_cards": int(d.card_id.nunique()),
                "profile": self.dev_profile.get(device_id)}

    def amount_recurrence(self, card_id, amount, tol, product, before_ts):
        d = self.tx.loc[self.by_card.get(card_id, [])]
        d = d[(d.ts < pd.Timestamp(before_ts)) & (d["product"] == product) & ((d.amount - amount).abs() <= tol)]
        return self._rows(d.index)

    def card_cases(self, card_id):
        return {"closed_cases": [self._case(c) for c in self._visible(self.case_by_card.get(card_id, []))],
                "agent_cases": [c for c in self._agent_cases() if c.get("card_id") == card_id or card_id in (c.get("connected_cards") or [])]}

    def threshold_scan(self, from_ts, to_ts, lo, hi):
        d = self.tx
        d = d[(d.channel == "online") & (d.amount >= lo) & (d.amount < hi) & (d.ts >= pd.Timestamp(from_ts)) & (d.ts <= pd.Timestamp(to_ts))]
        return [{"id": i, "ts": r.ts, "amount": r.amount, "card_id": r.card_id} for i, r in d.iterrows()]

    def shared_device_scan(self, from_ts, to_ts, min_cards, max_lifetime_cards):
        d = self.tx[(self.tx.channel == "online") & self.tx.device_id.notna()]
        w = d[(d.ts >= pd.Timestamp(from_ts)) & (d.ts <= pd.Timestamp(to_ts))]
        g = w.groupby("device_id").agg(win_cards=("card_id", lambda s: sorted(set(s))),
                                       new_flags=("device_status", lambda s: int((s == "New").sum())),
                                       proxy_flags=("proxy", lambda s: int(s.notna().sum())))
        g = g[g.win_cards.map(len) >= min_cards]
        life = d[d.device_id.isin(g.index)].groupby("device_id").card_id.nunique()
        g["life_cards"] = life
        g = g[g.life_cards <= max_lifetime_cards]
        return [{"device_id": k, "profile": self.dev_profile.get(k), "win_cards": v.win_cards, "life_cards": int(v.life_cards),
                 "new_flags": v.new_flags, "proxy_flags": v.proxy_flags} for k, v in g.iterrows()]

    # ---------- graph algorithm (mirrors USES_DEVICE + GDS tg_wcc) ----------
    def _wcc(self):
        if getattr(self, "_wcc_cache", None) is None:
            d = self.tx[self.tx.device_id.notna()]
            life = d.groupby("device_id").card_id.nunique()
            rare = set(life[life <= WCC_MAX_LIFETIME_CARDS].index)
            e = d[(d.channel == "online") & (d.ts >= pd.Timestamp(WCC_FROM)) & (d.ts <= pd.Timestamp(WCC_TO)) & d.device_id.isin(rare)]
            par = {}
            def find(x):
                par.setdefault(x, x)
                while par[x] != x: par[x] = par[par[x]]; x = par[x]
                return x
            for fp, dev in e[["fingerprint", "device_id"]].drop_duplicates().itertuples(index=False):
                a, b = find(("F", fp)), find(("D", dev))
                if a != b: par[max(a, b)] = min(a, b)
            comp = {}
            for x in list(par): comp.setdefault(find(x), []).append(x)
            ids = {root: i for i, root in enumerate(sorted(comp))}
            self._wcc_cache = ({x: ids[find(x)] for x in par}, {ids[r]: m for r, m in comp.items()})
        return self._wcc_cache

    def ring_component(self, device_id, max_list):
        of, members = self._wcc()
        cid = of.get(("D", device_id))
        if cid is None:
            return {"comp_id": -1, "n_fingerprints": 0, "n_devices": 0, "fingerprints": [], "devices": []}
        fps = sorted(x for k, x in members[cid] if k == "F"); devs = sorted(x for k, x in members[cid] if k == "D")
        small = len(fps) <= max_list
        return {"comp_id": cid, "n_fingerprints": len(fps), "n_devices": len(devs),
                "fingerprints": fps if small else [], "devices": devs if small else []}

    def wcc_ring_scan(self, min_devices, max_fingerprints):
        _, members = self._wcc(); out = []
        for cid, m in sorted(members.items()):
            fps = sorted(x for k, x in m if k == "F"); devs = sorted(x for k, x in m if k == "D")
            if len(devs) >= min_devices and len(fps) <= max_fingerprints:
                out.append({"comp_id": cid, "devices": devs, "fingerprints": fps})
        return out

    # ---------- vector store (mirrors TigerVector) ----------
    def upsert_vectors(self, kind, ids, matrix, payloads):
        self.vectors[kind] = (list(ids), np.asarray(matrix, dtype=np.float32), list(payloads))

    def vector_search(self, kind, query_vec, k):
        if kind not in self.vectors: return []
        ids, M, P = self.vectors[kind]
        q = np.asarray(query_vec, dtype=np.float32)
        sims = M @ q / (np.linalg.norm(M, axis=1) * (np.linalg.norm(q) + 1e-9) + 1e-9)
        if kind == "cases" and self.as_of is not None:
            vis = set(self._visible(ids)); sims = np.where([i in vis for i in ids], sims, -9)
        top = np.argsort(-sims)[:k]
        return [{"id": ids[i], "score": float(sims[i]), **P[i]} for i in top]

    # ---------- case memory ----------
    def _agent_cases(self):
        if self.as_of is not None: return []
        if not os.path.exists(self.agent_cases_path): return []
        with open(self.agent_cases_path) as f:
            return [json.loads(l) for l in f if l.strip()]

    def write_case(self, case_vertex, edges, embedding=None):
        rows = [c for c in self._agent_cases() if c["id"] != case_vertex["id"]]
        rows.append({**case_vertex, **edges})
        with open(self.agent_cases_path, "w") as f:
            for r in rows: f.write(json.dumps(r, default=str) + "\n")
        return {"written": case_vertex["id"]}
