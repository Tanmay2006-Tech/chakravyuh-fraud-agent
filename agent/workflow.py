"""Chakravyuh investigation workflow (LangGraph StateGraph; falls back to a plain sequential runner).

 intake -> gather_evidence -> recall_memory -> follow_up (LLM tool selection) -> assess
        -> decide_initial -> request_evidence (simulated, policy-approved) -> decide_final
        -> explain (GraphRAG + LLM) -> graph_algorithm (GDS tg_wcc, weight 0) -> write_memory (case written into TigerGraph)
"""
import re, time
import pandas as pd
import datetime, json
from . import detectors, assess, policy, simulator, narrate, gateway, graph_algorithm
from .policy import A
from .answer import dataset_ids

ORDER = ["ALLOW_TRANSACTION","DECLINE_TRANSACTION","VERIFY_WITH_CUSTOMER","STEP_UP_AUTH","BLOCK_CARD","BLOCK_ALL_CARDS","MONITOR_CARD",
         "CREATE_CASE","MONITOR_CONNECTED_CARDS","WARN_CUSTOMER","FILE_REPORT","ESCALATE_TO_ANALYST","GENERATE_REPORT","CLOSE_NO_FRAUD"]

def dedupe(acts):
    seen, out = set(), []
    for a in sorted(acts, key=lambda a: ORDER.index(a["action"])):
        if a["action"] not in seen: seen.add(a["action"]); out.append(a)
    return out

class Investigator:
    def __init__(self, toolbox, llm):
        self.tb, self.llm = toolbox, llm
        self.graph = self._compile()

    # ------------------------------------------------------------------ case record
    def event(self, s, stage, status, text, **extra):
        s.setdefault("timeline", []).append({"at": datetime.datetime.now().isoformat(timespec="seconds"), "stage": stage,
                                             "status": status, "text": text, **extra})

    def _vertex(self, s, status):
        c = s["case"]
        return {"id": f"INV-{c['case_id']}", "hhg_case": c["case_id"], "opened_at": str(c["opened_at"]), "case_status": status,
                "verdict": s.get("verdict") or "pending", "pattern": s.get("pattern") or "none", "probability": s.get("p1", s.get("p0", 0.0)),
                "exposure": s.get("exposure", 0.0), "sar_filed": bool(s.get("sar_file", False)), "summary": (s.get("summary") or "")[:2000],
                "final_actions": "|".join(a["action"] for a in s.get("final", [])), "timeline": json.dumps(s.get("timeline", []))[:60000]}

    # ------------------------------------------------------------------ nodes
    def intake(self, s):
        c = s["case"]
        s["ctx"] = self.tb.call("txn_context", txn_id=c["flagged_txn_id"])
        self.tb.note("intake", f"{c['trigger_type']} trigger on {c['card_id']}: {c['trigger_text']}")
        self.event(s, "opened", "open", f"Investigation INV-{c['case_id']} opened from " + {"risk_score": "a model risk score", "customer_report": "a customer report", "analyst_request": "an analyst request"}[c["trigger_type"]] + f" on payment {c['flagged_txn_id']}")
        return s

    def gather_evidence(self, s):
        s["signals"], s["facts"] = detectors.gather(self.tb, s["case"], s["ctx"])
        for sig in s["signals"]:
            self.tb.note("evidence", sig.claim, weight=sig.weight, family=sig.key)
        self.event(s, "evidence", "investigating", f"{len(s['signals'])} findings from the graph added to the case")
        return s

    def recall_memory(self, s):
        F, ctx = s["facts"], s["ctx"]
        mem = self.tb.call("card_cases", card_id=ctx["card_id"])
        s["card_cases"] = mem["closed_cases"]
        own = [m for m in (mem.get("agent_cases") or []) if m.get("id") != f"INV-{s['case']['case_id']}"]
        own += [m for m in (F.get("fp_agent_cases") or []) if m.get("id") not in {o.get("id") for o in own} and m.get("id") != f"INV-{s['case']['case_id']}"]
        s["own_memory"] = own
        fr = [m for m in own if m.get("verdict") == "fraud"]
        if fr:
            m = fr[0]
            sig = detectors.Signal("agent_memory", 1.0, f"An earlier Chakravyuh investigation, {m['id']} ({m.get('pattern', '').replace('_', ' ')}, {m.get('case_status', '')}), already linked this card to fraud",
                                   "query:card_cases (InvestigationCase memory)", [s["case"]["card_id"]])
            s["signals"].append(sig)
            self.tb.note("evidence", sig.claim, weight=sig.weight, family=sig.key)
        query = " ".join(x.claim for x in s["signals"][:5])
        s["vector_cases"] = self.tb.retrieve("cases", query, k=5)
        self.event(s, "memory", "investigating", f"Memory recalled: {len(mem['closed_cases'])} closed case(s) on the card, {len(F.get('fp_fraud_cases', []))} confirmed fraud on the fingerprint, {len(own)} earlier Chakravyuh investigation(s)")
        self.tb.note("memory", f"{len(mem['closed_cases'])} closed case(s) on this card; {len(F.get('fp_fraud_cases', []))} confirmed-fraud case(s) on this fingerprint; "
                     f"semantic matches: {', '.join(v['id'] for v in s['vector_cases'][:3])}")
        return s

    def follow_up(self, s):
        """Agentic step: the LLM (or a heuristic when no key is set) picks extra graph calls."""
        F, ctx = s["facts"], s["ctx"]
        linked = (F.get("device_ring") or {}).get("cards", []) or F.get("structuring_cards", [])
        menu = [{"tool": "card_cases", "args": {"card_id": "<a linked card>"}, "use": "does a linked card already have fraud cases?"},
                {"tool": "card_window", "args": {"card_id": ctx["card_id"], "hours_before": 72}, "use": "wider look at this card's recent activity"}]
        calls = None
        if self.llm.on and linked:
            digest = {"flagged": ctx["id"], "card": ctx["card_id"], "linked_cards": linked[:10], "signals": [x.claim for x in s["signals"]]}
            calls = self.llm.plan(digest, menu)
        if calls is None:
            calls = [{"tool": "card_cases", "args": {"card_id": c}, "why": "linked by shared origin"} for c in linked[:3]]
        s["follow_up"] = []
        for c in calls:
            if c["tool"] == "card_cases" and c.get("args", {}).get("card_id") in linked:
                r = self.tb.call("card_cases", card_id=c["args"]["card_id"])
                fr = [x["id"] for x in r["closed_cases"] if x["outcome"] == "confirmed_fraud"]
                s["follow_up"].append({"card": c["args"]["card_id"], "fraud_cases": fr, "why": c.get("why", "")})
            elif c["tool"] == "card_window":
                w = self.tb.call("card_window", card_id=ctx["card_id"], from_ts=ctx["ts"] - pd.Timedelta(hours=72), to_ts=ctx["ts"])
                s["follow_up"].append({"card": ctx["card_id"], "window_72h": len(w), "why": c.get("why", "")})
        if s["follow_up"]:
            self.tb.note("follow_up", "; ".join(f"{f['card']}: {len(f.get('fraud_cases', []))} prior fraud case(s)" for f in s["follow_up"] if "fraud_cases" in f) or "context widened")
        return s

    def assess_(self, s):
        c = s["case"]
        s["p0"] = assess.probability(c["trigger_type"], s["signals"])
        s["pos"] = assess.independent(s["signals"], +1); s["neg"] = assess.independent(s["signals"], -1)
        s["pattern"], s["pattern_desc"], s["episode"] = assess.pattern_and_episode(s["ctx"], s["signals"], s["facts"])
        s["episode0"] = list(s["episode"])
        self.tb.note("assess", f"fraud probability {s['p0']:.2f} from {s['pos']} independent fraud signal(s) and {s['neg']} legitimacy signal(s); leading hypothesis {s['pattern']}")
        return s

    def decide_initial(self, s):
        c, F, ctx, p = s["case"], s["facts"], s["ctx"], s["p0"]
        keys = {x.key for x in s["signals"]}
        dispute = c["trigger_type"] == "customer_report"
        ring = "shared_origin" in keys; struct = "structuring" in keys
        s["r7"] = dispute and F.get("recurring") and not (ring or struct or F.get("fp_fraud_cases"))
        exp_fraud = round(sum(x["amount"] for x in s["episode"]), 2)
        s["mode"], s["request"], s["initial"] = None, None, []
        rule_basis = "R2" if dispute else "R1 satisfied (multiple independent signals, p ≥ 0.85)"
        if s["r7"]:
            s["mode"] = "r7"
            s["initial"] = [A("CREATE_CASE", 0, "R7 / section 3a: customer disputes a charge"),
                            A("VERIFY_WITH_CUSTOMER", 0, "R7: the disputed charge matches the card's own recurring charge; confirm before any block"),
                            A("WARN_CUSTOMER", 0, "R7: remind the customer of the recurring charge")]
            s["request"] = "customer_validation"
        elif p <= 0.15 and s["neg"] >= 2 and (ctx["amount"] <= 500 or keys & {"fingerprint_history", "habit", "recurrence", "region"}):
            s["mode"] = "clear"
            s["initial"] = [A("ALLOW_TRANSACTION", 0, f"Section 6: probability {p:.2f} ≤ 0.15 on {s['neg']} independent pieces of legitimate-behaviour evidence"),
                            A("CLOSE_NO_FRAUD", 0, "Section 6 / R1: the model score is the only adverse signal; blocking would breach R1")]
        elif ring or struct:
            s["mode"] = "scheme"
            base = policy.fraud_actions(c["trigger_type"], exp_fraud, {}, "R2" if dispute else "R6")
            if exp_fraud > 1000: base.append(A("FILE_REPORT", exp_fraud, "Section 3a / R2: exposure exceeds $1,000"))
            s["initial"] = base
            s["request"] = "analyst_info"
        elif p >= 0.85 and s["pos"] >= 2:
            s["mode"] = "decisive"
            flags = {"card_testing": "card_testing" in keys}
            s["initial"] = policy.fraud_actions(c["trigger_type"], exp_fraud, flags, rule_basis)
        elif dispute and p >= 0.5:
            s["mode"] = "denied"
            s["initial"] = policy.fraud_actions(c["trigger_type"], exp_fraud, {"card_testing": "card_testing" in keys}, "R2")
        elif dispute:
            s["mode"] = "conflict"
            s["initial"] = [A("CREATE_CASE", 0, "Section 3a: customer dispute"), A("ESCALATE_TO_ANALYST", 0, "R8: the customer's denial conflicts with the graph evidence")]
        else:
            s["mode"] = "verify"
            online_new = ctx["channel"] == "online" and ctx.get("device_status") == "New"
            ask = "STEP_UP_AUTH" if online_new else "VERIFY_WITH_CUSTOMER"
            single = s["pos"] <= 1
            why = (f"R1: probability {p:.2f} is below 0.70" + (" and rests on a single signal" if single else "") + " — verify before any block") if p < 0.70 \
                  else f"Section 3b: probability {p:.2f} rests on one family of evidence; confirm with the cardholder before blocking"
            s["initial"] = [A(ask, 0, why),
                            A("CREATE_CASE", 0, "Section 3a: a case is opened whenever evidence is requested")]
            if p >= 0.65: s["initial"].insert(0, A("DECLINE_TRANSACTION", ctx["amount"], f"R1 / section 3b: probability {p:.2f} is high enough to hold the pending authorization while verification is outstanding"))
            s["request"] = "step_up_auth" if online_new else "customer_validation"
        s["initial"] = dedupe(s["initial"])
        self.tb.note("decide", "initial: " + ", ".join(f"{a['action']}[{a['route']}]" for a in s["initial"]))
        self.event(s, "assessed", "investigating", f"Fraud probability {s['p0']:.2f}; leading hypothesis {s['pattern'].replace('_', ' ')}")
        ex = gateway.dispatch(s["case"]["case_id"], [a for a in s["initial"] if not (s["request"] and a["action"] in ("VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH"))], "initial")
        s["executions"] = ex
        self.event(s, "initial_recommendation", "awaiting_evidence" if s["request"] else "decided",
                   "Initial recommendation: " + ", ".join(a["action"] for a in s["initial"]),
                   executed=[e["action"] for e in ex if e["status"] == "executed"], queued=[e["action"] for e in ex if e["status"] != "executed"])
        try:   # the case exists in the graph from this point, before any evidence comes back
            self.tb.call("write_case", case_vertex=self._vertex(s, "open"), edges={"card_id": s["case"]["card_id"], "txn_ids": [str(s["case"]["flagged_txn_id"])],
                         "connected_cards": [], "device_ids": [], "similar_cases": [], "pattern": s["pattern"]}, embedding=None)
        except Exception as e:
            self.tb.note("write_memory", f"open write failed: {e}")
        return s

    def request_evidence(self, s):
        s["requests"], s["response"] = [], None
        if not s["request"]: return s
        p = s["p0"]; F = s["facts"]
        if s["request"] == "analyst_info":
            linked = (F.get("device_ring") or {}).get("cards", []) or F.get("structuring_cards", [])
            elem = "the shared device profile" if F.get("device_ring") else "the repeated under-$500 run shape"
            outcome, text = simulator.analyst_ring(len(linked), elem)
        else:
            outcome, text = simulator.customer_validation(p, r7=s["r7"], ctx=s["ctx"])
            if s["request"] == "step_up_auth":
                text = {"deny": "Step-up challenge failed and the customer, when reached, states they did not make the transaction",
                        "confirm": "Customer passes the one-time passcode on their registered phone and confirms the purchase from a new device",
                        "no_reply": "Step-up challenge not completed and no reply from the customer within 24 hours"}[outcome]
        s["response"] = (outcome, text)
        s["requests"] = [{"type": s["request"], "asked_after_step": 5, "assumed_response": text}]
        self.tb.note("request", f"{s['request']} → assumed: {text}")
        ask = {"customer_validation": "VERIFY_WITH_CUSTOMER", "step_up_auth": "STEP_UP_AUTH"}.get(s["request"])
        if ask:
            s["executions"] += gateway.dispatch(s["case"]["case_id"], [{"action": ask, "route": "auto"}], "evidence_request")
        self.event(s, "evidence_requested", "awaiting_evidence", f"Requested {s['request'].replace('_', ' ')} through a policy-approved channel")
        self.event(s, "evidence_received", "investigating", f"Response (assumed): {text}")
        return s

    def decide_final(self, s):
        c, F, ctx, p = s["case"], s["facts"], s["ctx"], s["p0"]
        mode = s["mode"]; outcome = s["response"][0] if s["response"] else None
        episode = s["episode"]
        exp = round(sum(x["amount"] for x in episode), 2)
        verdict, p1, final = None, p, list(s["initial"])
        ring = F.get("device_ring"); struct = "structuring" in {x.key for x in s["signals"]}
        linked = (ring or {}).get("cards", []) or F.get("structuring_cards", [])
        if mode == "r7":
            verdict, p1 = "legitimate", min(p, 0.1)
            final = [A("CREATE_CASE", 0, "R7 / R3: case kept on file and closed with the customer's confirmation"),
                     A("WARN_CUSTOMER", 0, "R7: informational reminder about the recurring charge"),
                     A("CLOSE_NO_FRAUD", 0, "R3: customer confirmed the recurring charge is theirs")]
        elif mode == "clear":
            verdict = "legitimate"
        elif mode == "scheme":
            verdict, p1 = "fraud", max(p, 0.9)
            final += [A("MONITOR_CONNECTED_CARDS", exp, f"R6: {len(linked)} other card(s) share the {'device profile' if ring else 'scheme'}"),
                      A("FILE_REPORT", exp, "R6/R9 and section 3a: coordinated activity across customers")]
            if s["pattern"] == "undocumented": final.append(A("ESCALATE_TO_ANALYST", exp, "R9: undocumented, coordinated pattern"))
        elif mode in ("decisive", "denied"):
            verdict = "fraud"
        elif mode == "conflict":
            verdict = "uncertain"
        elif mode == "verify":
            if outcome == "deny":
                verdict, p1 = "fraud", round(1 - (1 - p) * 0.3, 2)
                final = policy.fraud_actions(c["trigger_type"], exp, {}, "R2")
            elif outcome == "confirm" and p >= 0.65:
                # the customer's word against strong graph evidence: do not simply close (R8)
                verdict = "uncertain"; exp = round(ctx["amount"], 2); episode = [ctx]
                final = [A("MONITOR_CARD", exp, "R8: the customer's confirmation conflicts with strong graph evidence"),
                         A("CREATE_CASE", exp, "Section 3a: case stays open while the conflict is reviewed"),
                         A("ESCALATE_TO_ANALYST", exp, "R8: evidence conflicts")]
            elif outcome == "confirm":
                verdict, p1 = "legitimate", round(p * 0.3, 2)
                final = [A("ALLOW_TRANSACTION", 0, "R3: customer confirmed"), A("CLOSE_NO_FRAUD", 0, "R3: customer confirmed; confirmation noted in the case file")]
            else:
                verdict = "uncertain"; exp = round(ctx["amount"], 2); episode = [ctx]
                final = [A("DECLINE_TRANSACTION", exp, "R4: no reply within 24 hours — decline pending authorizations"),
                         A("MONITOR_CARD", exp, "R4: no reply within 24 hours"),
                         A("CREATE_CASE", exp, "Section 3a: the case stays open pending the customer's reply")]
                if exp > 500: final.append(A("ESCALATE_TO_ANALYST", exp, f"R4/R8: uncertain with exposure ${exp:,.2f} > $500"))
        if verdict == "legitimate": episode, exp = [], 0.0
        if mode == "decisive" or mode == "denied":
            p1 = max(p, 0.85) if mode == "denied" else p
        shared = bool(ring) or bool(struct)
        file, why = policy.sar_needed(verdict, exp, shared, s["pattern"] == "undocumented")
        if file and not any(a["action"] == "FILE_REPORT" for a in final):
            final.append(A("FILE_REPORT", exp, why.replace("File: ", "")))
        if not file: final = [a for a in final if a["action"] != "FILE_REPORT"]
        # route every action on the final exposure
        final = dedupe([{**a, "route": policy.route(a["action"], exp)} for a in final])
        s["initial"] = dedupe([{**a, "route": policy.route(a["action"], round(sum(x["amount"] for x in s["episode"]), 2))} for a in s["initial"]])
        s.update(verdict=verdict, p1=round(p1, 2), final=final, episode=episode, exposure=exp, sar_file=file, sar_reason=why,
                 connected=sorted(set(linked)) if verdict == "fraud" else [])
        self.tb.note("decide", f"final ({verdict}, p={s['p1']:.2f}): " + ", ".join(f"{a['action']}[{a['route']}]" for a in final))
        if s.get("timeline") is not None and not s.get("_branch"):
            done = {e["action"] for e in s.get("executions", []) if e["status"] == "executed"}
            ex = gateway.dispatch(s["case"]["case_id"], [a for a in final if a["action"] not in done], "final", extra=len(s["connected"]))
            s["executions"] = s.get("executions", []) + ex
            self.event(s, "final_recommendation", "decided", f"Final recommendation ({verdict}, probability {s['p1']:.2f}): " + ", ".join(a["action"] for a in final),
                       executed=[e["action"] for e in ex if e["status"] == "executed"], queued=[e["action"] for e in ex if e["status"] != "executed"])
        return s

    def explain(self, s):
        c, ctx, F = s["case"], s["ctx"], s["facts"]
        rules = sorted({r for a in s["initial"] + s["final"] for r in re.findall(r"R\d+", a["reason"])}, key=lambda r: int(r[1:]))
        query = f"{s['pattern']} " + " ".join(rules) + " " + " ".join(x.claim for x in s["signals"][:3])
        s["docs"] = self.tb.retrieve("knowledge", query, k=6)
        devs, dev_ids = [], []
        if s["verdict"] == "fraud" or F.get("device_ring"):
            seen = {}
            if ctx.get("device_id") and ctx["channel"] == "online": seen[ctx["device_id"]] = ctx.get("device_profile")
            for x in s["episode"]:
                if x.get("device_id") and x["device_id"] not in seen and x["channel"] == "online":
                    seen[x["device_id"]] = self.tb.call("txn_context", txn_id=x["id"]).get("device_profile")
            dev_ids = [k for k, v in seen.items() if v]; devs = [seen[k] for k in dev_ids]
        s["devices"], s["device_ids"] = devs, dev_ids
        allowed = {c["case_id"], c["card_id"], c["customer_id"], str(c["flagged_txn_id"])} | {x["id"] for x in s["episode"]} | set(s["connected"])
        allowed |= {i for x in s["signals"] for i in x.entity_ids} | set(s.get("similar", []))
        written = None
        if self.llm.on:
            points = lambda w: "fraud" if w > 0.3 else "a genuine customer" if w < -0.3 else "neither way (weak)"
            bundle = {"case": {k: str(v) for k, v in c.items()}, "verdict": s["verdict"], "fraud_probability": s["p1"], "pattern": s["pattern"],
                      "case_status": self.case_status(s),
                      "pattern_description": s["pattern_desc"], "episode": [{**{k: str(x[k]) for k in ("id", "ts", "amount", "channel", "product")},   # region "na" means not recorded, not North America
                                  "billing_region": "not recorded" if str(x["region"]).lower() in ("na", "none", "") else str(x["region"])} for x in s["episode"]],
                      "affected_transaction_ids": [str(x["id"]) for x in s["episode"]],
                      "exposure_usd": s["exposure"], "evidence": [{"finding": x.claim, "points_to": points(x.weight)} for x in s["signals"]],
                      "connected_cards": s["connected"],
                      "device_profiles": devs, "evidence_requested": s["request"], "analyst_consulted": s["request"] == "analyst_info",
                      "assumed_response": s["response"][1] if s["response"] else None,
                      "final_actions": [a["action"] for a in s["final"]], "action_reasons": sorted({a["reason"] for a in s["initial"] + s["final"]}), "policy_passages": [d.get("content", "") for d in s["docs"][:4]],
                      "sar_required": s["sar_file"], "sar_subjects": dataset_ids([c["customer_id"], c["card_id"]] + s["connected"]) if s["sar_file"] else [],
                      "customer_id": c["customer_id"]}
            self.llm.last_rejects = []
            written = self.llm.write(bundle, allowed)
            if self.llm.last_rejects:   # the ID/rule guard kept the deterministic text for these parts
                self.tb.note("explain", "LLM text not used for: " + "; ".join(self.llm.last_rejects))
        resp = s["response"][1] if s["response"] else None
        s["summary"] = (written or {}).get("summary") or narrate.summary(c, s["verdict"], s["pattern"], s["p1"], s["episode"], s["signals"], s["final"], resp)
        s["sar_narrative"] = ""
        if s["sar_file"]:
            s["sar_narrative"] = (written or {}).get("sar_narrative") or narrate.sar(c, ctx, s["episode"], s["pattern"], s["pattern_desc"], s["connected"], devs, s["signals"], s["response"])
        s["writer"] = (written or {}).get("writer") or "template"   # the model that actually produced the text
        return s

    def graph_algorithm_(self, s):
        """Adds the tg_wcc component of the payment's device as weight-0 evidence (shown, never scored).
        Runs after decide/explain, so probability, actions, retrieval and the LLM input are untouched."""
        sig = graph_algorithm.component_signal(self.tb, s["ctx"])
        if sig is not None:
            s["signals"].append(sig)
            self.tb.note("evidence", sig.claim, weight=sig.weight, family=sig.key)
        return s

    @staticmethod
    def case_status(s):
        status = {"fraud": "closed_fraud", "legitimate": "closed_legitimate", "uncertain": "open"}[s["verdict"]]
        return "escalated" if any(a["action"] == "ESCALATE_TO_ANALYST" for a in s["final"]) else status

    def write_memory(self, s):
        c = s["case"]
        gid = f"INV-{c['case_id']}"
        status = self.case_status(s)
        s["status"], s["graph_case_id"] = status, gid
        self.event(s, "closed" if status.startswith("closed") else status, status,
                   {"closed_fraud": "Case closed as fraud", "closed_legitimate": "Investigation closed: not fraud",
                    "escalated": "Case escalated to a fraud analyst", "open": "Case left open pending the customer"}[status]
                   + (", suspicious activity report prepared" if s["sar_file"] else ""))
        vertex = self._vertex(s, status)
        edges = {"card_id": c["card_id"], "txn_ids": [x["id"] for x in s["episode"]] or [str(c["flagged_txn_id"])],
                 "connected_cards": s["connected"], "device_ids": s.get("device_ids", []),
                 "similar_cases": s["similar"], "pattern": s["pattern"]}
        emb = self.tb.emb.embed([s["summary"]])[0]
        try:
            self.tb.call("write_case", case_vertex=vertex, edges=edges, embedding=emb)
            s["written"] = True
            s["written_backend"] = self.tb.last_served
        except Exception as e:
            self.tb.note("write_memory", f"write failed: {e}"); s["written"] = False
        return s

    def select_similar(self, s):
        F = s["facts"]; pat = s.get("pattern")
        out = [c["id"] for c in F.get("fp_fraud_cases", [])] + [c["id"] for c in F.get("fp_cleared_cases", [])]
        if F.get("device_ring"):
            out += F["device_ring"]["undocumented_memory"][:3] + [c["id"] for c in F.get("device_cases", []) if c["outcome"] == "confirmed_fraud"][:2]
        elif any(x.key == "device_memory" for x in s["signals"]):
            out += [c["id"] for c in F.get("device_cases", []) if c["outcome"] == "cleared"][:3]
        out += [c["id"] for c in s["card_cases"] if c["pattern"] == pat][:2]
        out += [v["id"] for v in s["vector_cases"] if v.get("pattern") == pat and str(v["id"]).startswith("CC-")][:2]
        seen = []
        for x in out:
            if x not in seen: seen.append(x)
        s["similar"] = seen[:6]
        return s

    def branches(self, s):
        """Contingency plan: what the agent would recommend under every possible customer answer.
        Computed with the same policy engine, without extra graph calls."""
        if s.get("mode") != "verify": return []
        texts = {"deny": "Customer denies making the payment", "confirm": "Customer confirms the payment", "no_reply": "No reply within 24 hours"}
        out, note = [], self.tb.note
        self.tb.note = lambda *a, **k: None
        try:
            for o in ("deny", "confirm", "no_reply"):
                alt = {**s, "response": (o, texts[o]), "initial": list(s["initial"]), "episode": list(s["episode0"]), "_branch": True}
                alt = self.decide_final(alt)
                out.append({"if": texts[o], "verdict": alt["verdict"], "fraud_probability": alt["p1"],
                            "actions": [{"action": a["action"], "route": a["route"]} for a in alt["final"]], "report": alt["sar_file"],
                            "chosen": o == s["response"][0]})
        finally:
            self.tb.note = note
        return out

    # ------------------------------------------------------------------ graph
    def _steps(self):
        return [("intake", self.intake), ("gather_evidence", self.gather_evidence), ("recall_memory", self.recall_memory),
                ("follow_up", self.follow_up), ("assess", self.assess_), ("decide_initial", self.decide_initial),
                ("request_evidence", self.request_evidence), ("decide_final", self.decide_final),
                ("select_similar", self.select_similar), ("explain", self.explain), ("graph_algorithm", self.graph_algorithm_),
                ("write_memory", self.write_memory)]

    def _compile(self):
        try:
            from langgraph.graph import StateGraph, END
            g = StateGraph(dict)
            steps = self._steps()
            for name, fn in steps: g.add_node(name, fn)
            g.set_entry_point(steps[0][0])
            for (a, _), (b, _) in zip(steps, steps[1:]):
                if a == "decide_initial":
                    g.add_conditional_edges(a, lambda s: "request_evidence" if s["request"] else "decide_final",
                                            {"request_evidence": "request_evidence", "decide_final": "decide_final"})
                elif a == "request_evidence":
                    g.add_edge(a, b)
                else:
                    g.add_edge(a, b)
            g.add_edge(steps[-1][0], END)
            return g.compile()
        except ImportError:
            return None

    def run(self, case):
        self.tb.reset(); tok0 = self.llm.tokens; t0 = time.time()
        s = {"case": case, "request": None, "response": None, "requests": []}
        if self.graph is not None:
            s = self.graph.invoke(s)
        else:
            for name, fn in self._steps():
                if name == "request_evidence" and not s.get("request"): continue
                s = fn(s)
        s.setdefault("requests", [])
        s["branches"] = self.branches(s)
        s["tool_calls"], s["tokens"], s["latency_s"] = self.tb.calls, self.llm.tokens - tok0, round(time.time() - t0, 2)
        s["trace"] = list(self.tb.trace); s["backend"] = s.get("written_backend", self.tb.b.name)
        s["fallback_calls"] = sum(1 for x in s["trace"] if x.get("backend") == "local-fallback")
        s["engine"] = self.tb.b.name
        return s
