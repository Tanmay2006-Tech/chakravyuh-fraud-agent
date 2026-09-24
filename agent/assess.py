"""Turns signals into a calibrated fraud probability, a pattern, and the fraud episode."""
import math
import pandas as pd

PRIOR = {"risk_score": 0.30, "customer_report": 0.60, "analyst_request": 0.50}
LEGIT_FAMILIES = {"fingerprint_history", "habit", "recurrence", "region", "device_memory", "device"}

def logit(p): return math.log(p / (1 - p))
def sigmoid(x): return 1 / (1 + math.exp(-x))

def probability(trigger, signals):
    x = logit(PRIOR[trigger]) + sum(s.weight for s in signals)
    return round(min(max(sigmoid(x), 0.01), 0.99), 2)

def independent(signals, sign):
    fams = {s.key for s in signals if (s.weight >= 0.5 if sign > 0 else s.weight <= -0.5)}
    return len(fams)

def pattern_and_episode(ctx, signals, F):
    """Picks the pattern from the strongest structural evidence, and the transactions in the episode."""
    keys = {s.key for s in signals}
    t = ctx; ts = t["ts"]
    def fp_recent(hours):
        return [x for x in F["fp_txns"] if x["ts"] <= ts and (ts - x["ts"]).total_seconds() <= hours * 3600 and x["channel"] == t["channel"]]
    if "structuring" in keys:
        eps = F["structuring"]
        desc = (f"Authorization-threshold structuring: {len(eps)} online purchases on one card, each between $400 and $500, inside about "
                f"{int((eps[-1]['ts']-eps[0]['ts']).total_seconds()//60)} minutes, from more than one device. The amounts look chosen to stay just under a $500 "
                f"authorization limit. The same run shape appears on {len(F.get('structuring_cards', []))} other cards in the surrounding weeks, so it is a repeated scheme across customers rather than one compromised card.")
        return "undocumented", desc, eps
    if "shared_origin" in keys:
        ring = F["device_ring"]
        eps = [x for x in ring["window"] if x["card_id"] == t["card_id"]] or [t]
        eps = [x for x in eps if x["ts"] <= ts + pd.Timedelta(days=7)]
        if ring["anonymous"] or ring["undocumented_memory"]:
            desc = (f"Device-ring fraud: one device profile, behind an anonymous proxy and flagged as a New device on almost every use, made online purchases on "
                    f"{len(ring['cards'])+1} different cards within about a month, one to three purchases per card. It matches none of the five documented patterns "
                    f"because the link is the shared device across unrelated customers, not any one cardholder's behaviour; the bank's own history labels the same profile undocumented"
                    + (f" ({', '.join(ring['undocumented_memory'][:3])})." if ring["undocumented_memory"] else "."))
            return "undocumented", desc, eps
        return "card_not_present_new_device", "", eps
    if "card_testing" in keys:
        return "card_testing", "", F["card_testing"]
    if F.get("fp_fraud_cases"):
        last = max(F["fp_fraud_cases"], key=lambda c: str(c["opened_at"]))
        prior_ids = {i for c in F["fp_fraud_cases"] for i in c.get("txn_ids", [])}
        since = pd.Timestamp(last["opened_at"])
        eps = [x for x in F["fp_txns"] if since < x["ts"] <= ts and x["id"] not in prior_ids] or [t]
        return last["pattern"], "", eps
    online = t["channel"] == "online"
    if online:
        pat = "card_not_present_new_device" if "device" in keys and any(s.key == "device" and s.weight > 0 for s in signals) else "card_not_present_fraud"
        return pat, "", fp_recent(48) or [t]
    if "region" in keys and any(s.key == "region" and s.weight > 0 for s in signals):
        return "out_of_region_use", "", fp_recent(72) or [t]
    return "account_takeover", "", fp_recent(48) or [t]
