"""Evidence gathering. Each detector calls graph tools through the ToolBox and returns
Signal objects: a named piece of evidence, its log-odds weight, the entity IDs it rests on,
and the query that produced it. Weights are fixed and documented (docs/blog.md), not tuned per case."""
from dataclasses import dataclass, field
import json, math, os
import pandas as pd

_CAL = None
def calibration():
    """Weights learned from the bank's closed cases by scripts/calibrate.py (case memory)."""
    global _CAL
    if _CAL is None:
        p = os.path.join(os.path.dirname(__file__), "calibration.json")
        _CAL = json.load(open(p)) if os.path.exists(p) else {"weights": {}, "evidence": {}}
    return _CAL

def apply_calibration(signals, trigger):
    """For model-score alerts, blend each prior weight 50/50 with the weight learned from history,
    and say in the claim what the history showed. Customer reports keep policy weights: in the
    closed history every customer report was confirmed fraud, so there is nothing to learn there."""
    if trigger != "risk_score": return signals
    cal = calibration()
    for sg in signals:
        k = f"{sg.key}{'+' if sg.weight > 0 else '-'}"
        if k in cal["weights"]:
            ev = cal["evidence"][k]
            prior = sg.weight
            sg.weight = round((sg.weight + cal["weights"][k]) / 2, 2)
            stat = (f"{ev['fraud_rate_with']*100:.0f}% of {ev['n_with']} past model alerts with this signal were fraud "
                    f"(vs {ev['fraud_rate_without']*100:.0f}% without)")
            history_says_fraud = ev["fraud_rate_with"] > ev["fraud_rate_without"]
            if history_says_fraud == (prior > 0):
                sg.claim += f" — bank memory: {stat}"
            else:   # the bank's history points the other way from what the finding suggests: say so, and say how it counts
                effect = ("so here it counts for little" if abs(sg.weight) < 0.3 else
                          f"so here it counts towards {'fraud' if sg.weight > 0 else 'a genuine customer'}")
                sg.claim += f" — bank memory: less {'alarming' if prior > 0 else 'reassuring'} than it looks: {stat}, {effect}"
            sg.ref += " + calibration:closed_cases"
    return signals

H = pd.Timedelta(hours=1); D = pd.Timedelta(days=1)

@dataclass
class Signal:
    key: str            # family, used to count independent evidence
    weight: float       # log-odds contribution (+ fraud, - legitimate)
    claim: str
    ref: str
    entity_ids: list = field(default_factory=list)
    source: str = "graph"

def _day(t): return pd.Timestamp(t).normalize()

def gather(tb, case, ctx):
    """Runs the evidence sweep. Returns (signals, facts)."""
    S, F = [], {}
    t = ctx; ts = t["ts"]; card = t["card_id"]; amt = t["amount"]
    online = t["channel"] == "online"

    # ---- 1. card baseline ----
    prof = tb.call("card_profile", card_id=card, before_ts=ts)
    n = prof["n"] or 0
    mean = prof["total"] / n if n else 0
    std = math.sqrt(max(prof["sumsq"] / n - mean**2, 0)) if n else 0
    F.update(card_n=n, card_mean=round(mean, 2), card_std=round(std, 2), card_max=prof["max_amount"])
    if n >= 10 and amt > mean + 3 * std and amt > 3 * mean:
        S.append(Signal("amount", 0.7, f"${amt:,.2f} is far outside this card's history ({n} prior transactions, mean ${mean:,.2f}, max ${prof['max_amount']:,.2f})",
                        f"query:card_profile(card={card})", [t["id"], card]))

    # ---- 2. fingerprint (who is really using the card) ----
    fh = tb.call("fingerprint_history", fp=t["fingerprint"])
    prior = [x for x in fh["txns"] if x["ts"] < ts]
    F["fp_prior"] = len(prior); F["fp_txns"] = fh["txns"]
    fraud_cc = [c for c in fh["closed_cases"] if c["outcome"] == "confirmed_fraud"]
    clear_cc = [c for c in fh["closed_cases"] if c["outcome"] == "cleared"]
    F["fp_fraud_cases"] = fraud_cc; F["fp_cleared_cases"] = clear_cc
    F["fp_agent_cases"] = fh.get("agent_cases") or []
    if fraud_cc:
        S.append(Signal("fingerprint_history", 2.8,
            f"The same card fingerprint (card, billing region, account start, email) appears in {len(fraud_cc)} confirmed-fraud closed case(s): {', '.join(c['id'] for c in fraud_cc)} ({', '.join(sorted({c['pattern'] for c in fraud_cc}))})",
            f"query:fingerprint_history(fp={t['fingerprint']})", [c["id"] for c in fraud_cc]))
    elif clear_cc:
        S.append(Signal("fingerprint_history", -2.0, f"This card fingerprint was previously investigated and cleared: {', '.join(c['id'] for c in clear_cc)}",
                        f"query:fingerprint_history(fp={t['fingerprint']})", [c["id"] for c in clear_cc]))
    elif len(prior) >= 5 and (ts - prior[0]["ts"]) >= 30 * D:
        S.append(Signal("fingerprint_history", -1.5,
            f"Established cardholder: {len(prior)} earlier transactions under this fingerprint since {prior[0]['ts']:%Y-%m-%d}, none ever reported",
            f"query:fingerprint_history(fp={t['fingerprint']})", [x["id"] for x in prior[-3:]]))
    habitual = [x for x in prior if x["product"] == t["product"] and abs(x["amount"] - amt) <= 0.03 * amt]
    if len(habitual) >= 2 and not fraud_cc:
        S.append(Signal("habit", -1.2, f"Same cardholder has made {len(habitual)} earlier purchases at about ${amt:,.0f} under product {t['product']} (e.g. {', '.join(x['ts'].strftime('%m-%d') for x in habitual[-3:])})",
                        f"query:fingerprint_history(fp={t['fingerprint']})", [x["id"] for x in habitual[-3:]]))

    # ---- 3. recurrence of this exact charge on the card (dispute check, R7) ----
    tol = max(0.005 * amt, 0.15)
    rec = tb.call("amount_recurrence", card_id=card, amount=amt, tol=tol, product=t["product"], before_ts=ts)
    key_match = (lambda x: x["region"] == t["region"]) if not online else (lambda x: (x["p_email"] or "") == (t["p_email"] or ""))
    rec = [x for x in rec if key_match(x) and _day(x["ts"]) != _day(ts)]
    days = sorted({_day(x["ts"]) for x in rec})
    F["recurrence_days"] = [d.strftime("%Y-%m-%d") for d in days]
    recurring = len(days) >= 2 and (days[-1] - days[0]) >= 28 * D
    F["recurring"] = recurring
    if recurring and not fraud_cc:
        w = -1.8 if case["trigger_type"] == "customer_report" else -1.0
        S.append(Signal("recurrence", w, f"The card has paid the same ${amt:,.2f} (±${tol:.2f}, product {t['product']}, same {'region' if not online else 'email domain'}) on {len(days)} earlier days: {', '.join(F['recurrence_days'][-4:])}",
                        f"query:amount_recurrence(card={card}, amount={amt})", [x["id"] for x in rec[-4:]]))

    # ---- 4. local window on the card: bursts, card testing, threshold structuring ----
    win = tb.call("card_window", card_id=card, from_ts=ts - 3 * H, to_ts=ts + 1 * H)
    F["window"] = win
    online_w = [x for x in win if x["channel"] == "online"]
    # card testing: >=3 online < $5 within 60 min, followed by a larger purchase
    small = [x for x in online_w if x["amount"] < 5 and x["ts"] <= ts]
    for i in range(len(small)):
        grp = [y for y in small if 0 <= (y["ts"] - small[i]["ts"]).total_seconds() <= 3600]
        if len(grp) >= 3 and amt >= 5 and ts >= grp[-1]["ts"]:
            F["card_testing"] = grp + [t]
            S.append(Signal("card_testing", 2.5, f"{len(grp)} online authorizations under $5 within an hour, then a ${amt:,.2f} purchase",
                            f"query:card_window(card={card})", [y["id"] for y in grp] + [t["id"]])); break
    # threshold structuring: >=3 online $400-$500 within 40 minutes including the flagged one
    band = [x for x in online_w if 400 <= x["amount"] < 500 and abs((x["ts"] - ts).total_seconds()) <= 2400]
    if 400 <= amt < 500 and len(band) >= 3:
        F["structuring"] = band
        S.append(Signal("structuring", 3.0, f"{len(band)} online purchases between $400 and $500 within {int((band[-1]['ts']-band[0]['ts']).total_seconds()//60)} minutes (${sum(x['amount'] for x in band):,.2f} total), each just under a $500 authorization threshold",
                        f"query:card_window(card={card})", [x["id"] for x in band]))
        scan = tb.call("threshold_scan", from_ts=ts - 30 * D, to_ts=ts + 30 * D, lo=400.0, hi=500.0)
        runs = find_runs(scan, 2400, 3)
        others = sorted({r["card_id"] for r in runs if r["card_id"] != card and scheme_like(r)})
        F["structuring_cards"] = others; F["structuring_runs"] = runs
        if others:
            S.append(Signal("structuring_network", 0.5, f"The same under-$500 run shape appears on {len(others)} other cards within ±30 days ({', '.join(others[:6])}{'…' if len(others) > 6 else ''})",
                            "query:threshold_scan(±30d, $400-$500)", others))
    # near-identical amounts from several different fingerprints within 30 minutes (scripted replay)
    twins = [x for x in online_w if x["id"] != t["id"] and abs((x["ts"] - ts).total_seconds()) <= 1800
             and abs(x["amount"] - amt) <= 0.01 * amt and x["fingerprint"] != t["fingerprint"]]
    if online and len(twins) >= 2:
        S.append(Signal("replay", 1.0, f"{len(twins)} other charges of almost the same amount (${', $'.join(format(x['amount'], '.2f') for x in twins)}) hit this card within 30 minutes under different fingerprints",
                        f"query:card_window(card={card})", [x["id"] for x in twins]))
    # same-fingerprint burst of similar amounts
    burst = [x for x in fh["txns"] if x["id"] != t["id"] and abs((x["ts"] - ts).total_seconds()) <= 2 * 3600 and abs(x["amount"] - amt) <= 0.05 * amt]
    if online and len(burst) >= 2:
        S.append(Signal("burst", 0.5, f"Burst: {len(burst)+1} purchases of about ${amt:,.0f} under this fingerprint within two hours",
                        f"query:fingerprint_history(fp={t['fingerprint']})", [x["id"] for x in burst] + [t["id"]]))

    # ---- 5. device and identity ----
    F["device_ring"] = None
    if online and t.get("device_id"):
        known = t["device_id"] in set(prof["devices"])
        nb = tb.call("device_neighbors", device_id=t["device_id"], from_ts=ts - 15 * D, to_ts=ts + 15 * D)
        wcards = sorted({x["card_id"] for x in nb["window"]})
        newshare = sum(x["device_status"] == "New" for x in nb["window"]) / max(len(nb["window"]), 1)
        proxyshare = sum(bool(x["proxy"]) for x in nb["window"]) / max(len(nb["window"]), 1)
        F.update(device_window_cards=wcards, device_lifetime_cards=nb["lifetime_cards"], device_lifetime_txns=nb["lifetime_txns"])
        rare = nb["lifetime_cards"] <= 15
        anon = proxyshare >= 0.8 and newshare >= 0.8
        # a device *profile* is a model/OS/browser string, so popular phones are naturally shared.
        # A rare profile only counts as a shared origin when the other cards' activity is coherent
        # with this one (same email domain and an amount within 10%).
        coherent = {x["card_id"] for x in nb["window"] if x["card_id"] != card and (x["p_email"] or "") == (t["p_email"] or "")
                    and abs(x["amount"] - amt) <= 0.10 * amt}
        F["device_coherent_cards"] = sorted(coherent)
        if len(wcards) >= 3 and (anon or (rare and len(coherent) >= 2)):
            others = [c for c in wcards if c != card]
            F["device_ring"] = {"device_id": t["device_id"], "cards": others, "window": nb["window"], "anonymous": anon,
                                "undocumented_memory": [c["id"] for c in nb["closed_cases"] if c["pattern"] == "undocumented"]}
            desc = ("behind an anonymous proxy and marked New on almost every use" if anon else
                    f"a rare profile (only {nb['lifetime_cards']} cards in its lifetime) where {len(coherent)} other cards show the same email domain and an amount within 10% of ${amt:,.2f}")
            S.append(Signal("shared_origin", 2.5, f"Shared origin: this device profile ({t.get('device_profile')}) touched {len(wcards)} different cards within ±15 days — {desc}",
                            f"query:device_neighbors(dev={t['device_id']}, ±15d)", others[:12] + [t["device_id"]]))
        if t.get("device_status") == "New" and (not known or F["device_ring"]):
            S.append(Signal("device", 0.6, f"Device profile marked New for this account ({t.get('device_profile')})" + ("" if known else " and never seen on the card before"),
                            f"query:txn_context(t={t['id']})", [t["device_id"]]))
        elif (known or t.get("device_status") == "Found") and not F["device_ring"]:
            S.append(Signal("device", -0.3, "Device profile is already known on this card (identity record: Found)", f"query:card_profile(card={card})", [t["device_id"]]))
        cc = nb["closed_cases"]
        cl = [c for c in cc if c["outcome"] == "cleared"]; fr = [c for c in cc if c["outcome"] == "confirmed_fraud"]
        F["device_cases"] = cc
        if not F["device_ring"] and len(cl) >= 2 and len(cl) >= len(fr):
            S.append(Signal("device_memory", -0.7, f"Past alerts on this device profile were mostly cleared ({', '.join(c['id'] for c in cl[:4])}), e.g. customers confirming purchases from new phones",
                            f"query:device_neighbors(dev={t['device_id']})", [c["id"] for c in cl[:4]]))
        if t.get("proxy") in ("IP_PROXY:ANONYMOUS", "IP_PROXY:HIDDEN"):
            S.append(Signal("proxy", 0.3, f"Connection behind a proxy ({t['proxy']})", f"query:txn_context(t={t['id']})", [t["id"]]))
    if (t.get("p_email") or "") == "anonymous.com":
        S.append(Signal("email", 0.3, "Purchaser email domain is anonymous.com", f"query:txn_context(t={t['id']})", [t["id"]]))

    # ---- 6. geography (card-present) ----
    if not online:
        rd = prof["region_days"].get(t["region"], 0)
        F["region_days"] = rd
        if rd >= 5 and not fraud_cc:
            S.append(Signal("region", -0.8, f"Billing region {t['region']} is routine for this card: purchases there on {rd} different days before", f"query:card_profile(card={card})", [card]))
        elif rd == 0 and not fraud_cc:
            S.append(Signal("region", 1.3, f"Card-present purchase in billing region {t['region']}, where the card has no history", f"query:card_profile(card={card})", [card, t["id"]]))

    # ---- 7. the model score (an input, never a verdict) ----
    if case["trigger_type"] == "risk_score" and t.get("risk_score") is not None:
        S.append(Signal("model_score", round(1.2 * (t["risk_score"] - 0.5), 2), f"Bank model scored the transaction {t['risk_score']:.2f} (treated as a weak input: above 0.7 most alerts are legitimate)",
                        f"case_pack:{case['case_id']}", [t["id"]], source="external"))
    return apply_calibration(S, case["trigger_type"]), F

def find_runs(rows, max_gap_s, min_n):
    rows = sorted(rows, key=lambda r: (r["card_id"], r["ts"]))
    runs, cur = [], []
    for r in rows:
        if cur and (r["card_id"] != cur[-1]["card_id"] or (r["ts"] - cur[0]["ts"]).total_seconds() > max_gap_s):
            if len(cur) >= min_n: runs.append(cur)
            cur = []
        cur.append(r)
    if len(cur) >= min_n: runs.append(cur)
    return [{"card_id": g[0]["card_id"], "start": g[0]["ts"], "n": len(g), "total": round(sum(x["amount"] for x in g), 2), "amounts": [x["amount"] for x in g], "txn_ids": [x["id"] for x in g]} for g in runs]

def scheme_like(run):
    """The structuring shape seen in the bank's own undocumented cases: several varied amounts
    just under $500 (averaging $450+), not a repeated identical price."""
    import statistics
    a = run["amounts"]
    return len(a) >= 3 and statistics.mean(a) >= 450 and statistics.pstdev(a) > 3
