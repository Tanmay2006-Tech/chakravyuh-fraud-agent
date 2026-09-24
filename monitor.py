"""Optional (Innovation): the agent watches the exam period on its own. Portfolio sweeps find
coordinated schemes the case pack never mentions, then each hit is investigated by the same
workflow as a self-raised analyst_request. Output: monitoring/<id>.json + monitoring/alerts.json
   python monitor.py --backend tigergraph --from 2016-11-01 --to 2016-12-31
"""
import argparse, json, os, time
import pandas as pd
from agent.runtime import build
from agent.answer import build as build_answer
from agent.detectors import find_runs, scheme_like
from agent.graph_algorithm import card_of

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="tigergraph", choices=["tigergraph", "local"])
    ap.add_argument("--embedder", default="fastembed")
    ap.add_argument("--from", dest="start", default="2016-11-01"); ap.add_argument("--to", dest="end", default="2016-12-31 23:59:59")
    ap.add_argument("--max", type=int, default=15)
    ap.add_argument("--pause", type=float, default=5, help="seconds to sleep between cases (stays under free-tier LLM per-minute limits)")
    a = ap.parse_args()
    inv = build(a.backend, a.embedder); tb = inv.tb
    pack = pd.read_csv("data/local/case_pack.csv"); known = set(pack.card_id)
    alerts = []
    runs = find_runs(tb.call("threshold_scan", from_ts=pd.Timestamp(a.start), to_ts=pd.Timestamp(a.end), lo=400.0, hi=500.0), 2400, 3)
    for r in runs:
        if r["n"] >= 4 and scheme_like(r):
            alerts.append({"kind": "threshold_structuring", "card_id": r["card_id"], "txn": r["txn_ids"][-1],
                           "why": f"{r['n']} online purchases of $400-$500 within 40 minutes (${r['total']:,.2f})"})
    for d in tb.call("shared_device_scan", from_ts=pd.Timestamp(a.start), to_ts=pd.Timestamp(a.end), min_cards=4, max_lifetime_cards=80):
        if d["new_flags"] < 0.8 * len(d["win_cards"]) or d["proxy_flags"] < 0.8 * len(d["win_cards"]): continue
        alerts.append({"kind": "shared_device", "device_id": d["device_id"], "cards": d["win_cards"],
                       "why": f"rare device profile ({d['profile']}) used on {len(d['win_cards'])} cards in the window, {d['new_flags']} flagged New"})
    os.makedirs("monitoring", exist_ok=True)
    json.dump(alerts, open("monitoring/alerts.json", "w"), indent=2, default=str)
    print(f"{len(alerts)} alerts raised by portfolio sweeps")
    # graph algorithm sweep: GDS tg_wcc components that tie cards-in-use to SEVERAL rare devices. The single-device
    # scan above cannot see these; they are listed for analysts (monitoring/wcc_rings.json), not auto-investigated.
    rings = []
    for r in tb.call("wcc_ring_scan", min_devices=2, max_fingerprints=200):
        cards = sorted({card_of(f) for f in r["fingerprints"]})
        if len(cards) < 3: continue
        rings.append({"comp_id": r["comp_id"], "n_cards": len(cards), "n_fingerprints": len(r["fingerprints"]), "n_devices": len(r["devices"]),
                      "devices": r["devices"], "cards": cards, "case_pack_cards": sorted(set(cards) & known)})
    rings.sort(key=lambda x: (-x["n_devices"], -x["n_cards"]))
    json.dump(rings, open("monitoring/wcc_rings.json", "w"), indent=2)
    print(f"tg_wcc: {len(rings)} multi-device rings of 3+ cards (largest: " +
          ", ".join(f"{x['n_cards']} cards/{x['n_devices']} devices" for x in rings[:3]) + ") -> monitoring/wcc_rings.json")
    n = 0
    for al in alerts:
        if n >= a.max: break
        if al["kind"] == "threshold_structuring":
            if al["card_id"] in known: continue
            card, txn = al["card_id"], al["txn"]
        else:
            w = tb.call("device_neighbors", device_id=al["device_id"], from_ts=pd.Timestamp(a.start), to_ts=pd.Timestamp(a.end))["window"]
            w = [x for x in w if x["card_id"] not in known]
            if not w: continue
            card, txn = w[0]["card_id"], w[0]["id"]
        ctx = tb.call("txn_context", txn_id=txn)
        if n and a.pause: time.sleep(a.pause)
        n += 1
        case = {"case_id": f"MON-{n:03d}", "opened_at": str(ctx["ts"]), "trigger_type": "analyst_request", "flagged_txn_id": str(txn),
                "card_id": card, "customer_id": card.split("-")[0], "risk_score": None,
                "trigger_text": f"Self-raised by Chakravyuh portfolio sweep: {al['why']}. Review transaction {txn} on card {card}."}
        s = inv.run(case); ans = build_answer(s)
        json.dump(ans, open(f"monitoring/{case['case_id']}.json", "w"), indent=2, default=str)
        print(case["case_id"], card, ans["case"]["verdict"], ans["case"]["pattern"], ans["case"]["exposure_usd"], ans["sar"]["file"])
    print(f"LLM: {inv.llm.retries} same-provider retries, {inv.llm.failovers} failovers")

if __name__ == "__main__":
    main()
