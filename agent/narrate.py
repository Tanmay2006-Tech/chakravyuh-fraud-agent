"""Deterministic writing used when no LLM key is configured, and as the guard-rail fallback."""
def fmt_money(x): return f"${x:,.2f}"

import re as _re
def short(claim, n=240):
    base, _, mem = claim.partition(" — bank memory: ")
    base = _re.sub(r" \([^()]*\|[^()]*\)", "", base).rstrip(".")      # drop long device strings
    if len(base) > n: base = base[:n].rsplit(" ", 1)[0].rstrip(",;:(") + "…"
    if mem:
        m = _re.search(r"(\d+)% of (\d+)", mem)
        if m: base += f" (in the bank's history only {m.group(1)}% of {m.group(2)} such alerts were fraud)" if int(m.group(1)) < 50 else f" (in the bank's history {m.group(1)}% of {m.group(2)} such alerts were fraud)"
    return base

def summary(case, verdict, pattern, p, eps, signals, actions_final, response):
    lean = [x for x in signals if (x.weight > 0) == (verdict == "fraud") and x.key != "model_score"] or \
           [x for x in signals if x.key != "model_score"] or signals
    top = sorted(lean, key=lambda s: -abs(s.weight))[:2]
    why = "; ".join(short(s.claim) for s in top)
    head = {"fraud": f"Assessed as fraud ({pattern.replace('_', ' ')}), probability {p:.2f}.",
            "legitimate": f"Assessed as legitimate, probability of fraud {p:.2f}.",
            "uncertain": f"Left uncertain at probability {p:.2f}."}[verdict]
    parts = [f"{case['case_id']} ({case['trigger_type'].replace('_', ' ')}) on card {case['card_id']}, flagged transaction {case['flagged_txn_id']}. {head}",
             f"Key evidence: {why}."]
    if verdict == "fraud":
        parts.append(f"The episode covers {len(eps)} transaction(s) from {eps[0]['ts']:%Y-%m-%d} to {eps[-1]['ts']:%Y-%m-%d}, exposure {fmt_money(sum(x['amount'] for x in eps))}.")
    if response: parts.append(f"Evidence requested; assumed response: {response}.")
    parts.append("Next: " + ", ".join(a["action"] for a in actions_final) + ".")
    return " ".join(parts)

def sar(case, ctx, eps, pattern, desc, connected, devices, signals, response=None):
    first, last = eps[0]["ts"], eps[-1]["ts"]
    total = sum(x["amount"] for x in eps)
    chans = sorted({x["channel"].replace("_", "-") for x in eps})
    regions = sorted({str(x["region"]) for x in eps if x["region"] not in (None, "na")})
    lines = [
        (f"On {first:%Y-%m-%d} at {first:%H:%M}" if first == last else f"Between {first:%Y-%m-%d %H:%M} and {last:%Y-%m-%d %H:%M}") + f", card {case['card_id']} held by customer {case['customer_id']} was used for {len(eps)} {'/'.join(chans)} transaction(s) totalling {fmt_money(total)} (transaction IDs {', '.join(x['id'] for x in eps)}).",
        f"The activity came to the bank's attention through {'an' if case['trigger_type'][0] in 'aeiou' else 'a'} {case['trigger_type'].replace('_', ' ')} on {case['opened_at']}, referring to transaction {case['flagged_txn_id']} for {fmt_money(ctx['amount'])}.",
    ]
    if regions: lines.append(f"The transactions were billed in region code(s) {', '.join(regions)} (country code 87 unless noted), and the purchaser email domain was {ctx.get('p_email') or 'not recorded'}.")
    if devices: lines.append(f"Online activity used the device profile {'; '.join(devices)}.")
    if pattern == "undocumented":
        lines.append(f"The activity fits none of the bank's five documented fraud patterns. {desc}")
    else:
        lines.append(f"The investigation identified the activity as {pattern.replace('_', ' ')}.")
        for s in sorted([s for s in signals if s.weight > 0 and s.key != "model_score"], key=lambda s: -s.weight)[:3]:
            lines.append(short(s.claim, 300) + ".")
    if connected: lines.append(f"The same scheme links to other cards, which have been placed under monitoring: {', '.join(connected)}.")
    if case["trigger_type"] == "customer_report":
        who = "The cardholder reported the activity as unauthorised."
    elif response and response[0] == "deny":
        who = "When contacted, the cardholder stated they did not make the transaction."
    elif response:
        who = f"{response[1]}."
    else:
        who = "The graph evidence alone establishes the activity as fraudulent."
    lines.append(f"{who} The card has been recommended for blocking and reissue, and the total suspicious amount reported is {fmt_money(total)}.")
    return " ".join(lines)
