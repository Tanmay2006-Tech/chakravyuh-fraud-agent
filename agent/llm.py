"""LLM layer (OpenAI-compatible: Groq or Gemini). The LLM never decides the verdict: it chooses
follow-up graph tools, and writes the case summary and SAR narrative from the evidence bundle and
retrieved policy passages. Every ID it writes is checked against the case's evidence; if it
invents one, the deterministic template is used instead.

Failover: LLM_PROVIDER is tried first, then the others that are configured (an optional second Groq model,
then Gemini). A temporary error (429, 503) is retried on the same provider up to twice with backoff (Retry-After
if given, else ~20s then ~45s) before failing over; any other API error fails over straight away. Circuit breaker: a hard error
(401, 403, 404, or a 400 other than JSON validation) disables a provider for the run; 4 consecutive
failed requests put it on a 90-second cooldown. If every provider fails, the error is raised and
plan()/write() fall back to the template exactly as before."""
import json, os, re, sys, time

ASCII_PUNCT = str.maketrans({**{c: "-" for c in "‐‑‒−"}, " ": " ", " ": " "})  # hyphen-like dashes, no-break spaces
ID_RE = re.compile(r"\b(?:\d{7}|C\d{5}(?:-K\d)?|CC-\d{4}|D[0-9a-f]{10})\b")

PROVIDERS = {  # name -> (key env, model env, default model, base_url); tried in this order after LLM_PROVIDER
    "groq": ("GROQ_API_KEY", "GROQ_MODEL", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1"),
    # optional second Groq model: Groq's per-minute token budget is per model, so a rate-limited request on the
    # primary can go straight to it. Off unless GROQ_BACKUP_MODEL is set.
    "groq-backup": ("GROQ_API_KEY", "GROQ_BACKUP_MODEL", None, "https://api.groq.com/openai/v1"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "gemini-3.6-flash", "https://generativelanguage.googleapis.com/v1beta/openai/"),
}
TEMPORARY = {429, 503}          # retried on the same provider before failing over
HARD = {400, 401, 403, 404}     # trip the breaker at once (except 400 json_validate_failed)
BACKOFF = [20, 45]              # seconds before same-provider retry 1 and 2 (per-minute token windows), unless Retry-After says otherwise
MAX_FAILS, COOLDOWN = 4, 90     # consecutive failed requests before a provider cools down, and for how long (s)
_sleep = time.sleep
# Reasoning models spend max_tokens on hidden thinking first; at default effort that can leave no room for
# the JSON answer (Groq: 400 json_validate_failed, Gemini: truncated JSON). Low effort keeps the budget for the output.
REASONING = re.compile(r"gpt-oss|gemini-(2\.5|[3-9])")

def _extra(model):
    return {"reasoning_effort": "low"} if REASONING.search(model) else {}

class LLM:
    def __init__(self):
        first = os.getenv("LLM_PROVIDER", "groq").lower()
        order = [first] + [p for p in PROVIDERS if p != first] if first in PROVIDERS else list(PROVIDERS)
        self.providers, self.tokens, self.last_model = [], 0, None
        self.retries = self.failovers = 0   # run totals, for reporting
        self.last_rejects = []              # why the last write() output was (partly) not used
        try:
            from openai import OpenAI
            for i, p in enumerate(order):
                key_env, model_env, default, url = PROVIDERS[p]
                if not os.getenv(key_env): continue
                model = os.getenv(model_env) or default
                if i == 0 and os.getenv("LLM_MODEL"): model = os.getenv("LLM_MODEL")   # legacy override, primary only
                if not model: continue                                                   # optional provider not configured
                self.providers.append({"name": p, "model": model, "fails": 0, "dead": False, "until": 0.0,
                                       "client": OpenAI(api_key=os.getenv(key_env), base_url=url, timeout=60, max_retries=0)})
        except ImportError:
            pass
        self.client = self.providers[0]["client"] if self.providers else None
        self.model = self.providers[0]["model"] if self.providers else None
        self.name = self.model if self.client else "template (no LLM key set)"

    @property
    def on(self): return self.client is not None

    @staticmethod
    def _retry_after(e, attempt):
        try: return min(float(e.response.headers.get("retry-after")), 60.0)
        except Exception: return BACKOFF[attempt]

    def _available(self, p):
        if p["dead"]: return False
        if p["until"] and time.time() >= p["until"]:
            p["until"], p["fails"] = 0.0, 0
            print(f"[llm] {p['name']} cooldown over; using it again", file=sys.stderr)
        return not p["until"]

    def _call(self, p, kw):
        """One request on one provider, retrying 429/503 on the same provider. Returns the response or raises."""
        from openai import APIError
        for attempt in range(len(BACKOFF) + 1):
            try:
                return p["client"].chat.completions.create(model=p["model"], **kw, **_extra(p["model"]))
            except APIError as e:
                code = getattr(e, "status_code", None)
                print(f"[llm] warning: {p['name']} failed ({type(e).__name__}, HTTP {code or '-'})", file=sys.stderr)
                if code in TEMPORARY and attempt < len(BACKOFF):
                    self.retries += 1; _sleep(self._retry_after(e, attempt)); continue
                raise

    def chat(self, system, user, as_json=False, max_tokens=900):
        from openai import APIError
        kw = {"temperature": 0.1, "max_tokens": max_tokens,
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if as_json: kw["response_format"] = {"type": "json_object"}
        live = [p for p in self.providers if self._available(p)]
        if not live: raise RuntimeError("no LLM provider available")
        last = None
        for i, p in enumerate(live[:2]):   # the current provider, then the next one
            if i: self.failovers += 1
            try:
                r = self._call(p, kw)
            except APIError as e:
                last, code = e, getattr(e, "status_code", None)
                json_fail = code == 400 and "json_validate_failed" in str(getattr(e, "body", "") or "")
                if code in HARD and not json_fail:
                    p["dead"] = True
                    print(f"[llm] {p['name']} returned HTTP {code}; disabling it for the rest of this run", file=sys.stderr)
                else:
                    p["fails"] += 1
                    if p["fails"] >= MAX_FAILS:
                        p["until"] = time.time() + COOLDOWN
                        print(f"[llm] {p['name']} failed {MAX_FAILS} times in a row; cooling down for {COOLDOWN}s", file=sys.stderr)
                continue
            p["fails"] = 0
            self.tokens += getattr(r.usage, "total_tokens", 0) or 0
            self.last_model = p["model"]
            return r.choices[0].message.content
        raise last

    # ---------- tool selection ----------
    def plan(self, digest, menu):
        sys = ("You are a fraud investigator choosing follow-up graph queries. Reply with JSON {\"calls\":[{\"tool\":...,\"args\":{...},\"why\":...}]}. "
               "Choose at most 3 calls from the menu, only if they could change the decision. Use only IDs present in the digest.")
        try:
            out = json.loads(self.chat(sys, json.dumps({"digest": digest, "menu": menu}, default=str, ensure_ascii=False), as_json=True, max_tokens=400))
            return [c for c in out.get("calls", []) if c.get("tool") in {m["tool"] for m in menu}][:3]
        except Exception:
            return None

    # ---------- writing ----------
    @staticmethod
    def breaks_rules(t, bundle, key):
        low = t.lower(); status = bundle.get("case_status", "")
        if re.search(r"\bclosed\b", low) and not status.startswith("closed"): return "says closed"
        if re.search(r"\b(the|an|a|our) (fraud )?analysts? (reviewed|verified|confirmed|followed|found|applied|determined|checked|decided|approved|assessed|contacted|noted|concluded|cleared|closed|allowed)\b", low) and not bundle.get("analyst_consulted"): return "credits an analyst with the agent's work"
        if re.search(r"\b(utc|gmt|local time)\b", low): return "adds a time zone"
        if re.search(r"\b(preceding|prior|previous|past|last) \d+ days\b", low) and "±" in json.dumps(bundle.get("evidence"), ensure_ascii=False): return "one-sided window"
        if re.search(r"\b(closed_fraud|closed_legitimate|case_status|analyst_consulted|sar_required|points_to|affected_transaction_ids|ALLOW_TRANSACTION|DECLINE_TRANSACTION|MONITOR_CARD|MONITOR_CONNECTED_CARDS|WARN_CUSTOMER|VERIFY_WITH_CUSTOMER|STEP_UP_AUTH|BLOCK_CARD|BLOCK_ALL_CARDS|GENERATE_REPORT|CREATE_CASE|FILE_REPORT|ESCALATE_TO_ANALYST|CLOSE_NO_FRAUD)\b", t): return "quotes internal field names or codes"
        allowed_rules = set(re.findall(r"R\d+", " ".join(bundle.get("action_reasons", []))))
        cited = set(re.findall(r"\bR\d+\b", t))
        if cited - allowed_rules: return f"cites {', '.join(sorted(cited - allowed_rules))}, which no action rests on"
        if key == "summary" and bundle.get("evidence_requested") and not re.search(r"ask|verif|passcode|one-time|confirm|repl|contact|respond|analyst|step-up|authenticat", low):
            return "does not say what was asked"
        if key == "sar_narrative":
            if f"{bundle.get('exposure_usd', 0):,.2f}" not in t: return "omits the total amount"
            if re.search(r"\b(this|the) (sar|report|narrative) (includes|lists|contains|names)\b", low): return "talks about itself"
            missing = [i for i in bundle.get("sar_subjects", []) + bundle.get("affected_transaction_ids", []) if i not in t]
            if missing: return f"omits {len(missing)} subject/transaction ID(s)"
        return None

    def write(self, bundle, allowed_ids):
        """Returns {"summary", "sar_narrative"?, "writer": <model that produced it>} or None (-> template)."""
        sys = ("You write for a bank fraud team. Using ONLY the facts in the bundle, return JSON with keys "
               "'summary' (2-6 short, plain sentences for the bank's fraud team: what happened, the verdict and why, what happens next) and, "
               "if bundle.sar_required is true, 'sar_narrative' (6-12 sentences following FinCEN narrative guidance: who, what, when, where, "
               "how, why suspicious; self-contained). Rules: "
               "1) Never invent IDs, amounts, dates or times. "
               "2) Each evidence item says which way it points (points_to); never describe it as pointing the other way. "
               "3) Quote windows and statistics with their exact meaning: '±15 days' means 15 days either side, not 'the preceding 15 days'; "
               "'X% of past model alerts with this signal were fraud' is not 'appears in X% of fraud alerts'. "
               "4) Use case_status for the state of the case: say 'closed' only if it starts with 'closed_'; if it is 'escalated', say the case "
               "was escalated to a fraud analyst; if 'open', say it stays open. "
               "5) The investigation was done by the agent (call it 'the investigation' or 'Chakravyuh'), not by an analyst: say an analyst did something only if analyst_consulted is true; if ESCALATE_TO_ANALYST is a final action, say the case goes to an analyst. "
               "6) Timestamps have no time zone: never add one (no UTC, no local time). "
               "7) In the SAR narrative, name every ID in sar_subjects and every transaction ID in affected_transaction_ids (a closing sentence listing the linked cards is fine). "
               "8) Write plain English for people: never quote field names or codes from the bundle (no closed_fraud, case_status, points_to, BLOCK_CARD); say 'the card was blocked', 'the case was closed as fraud'. "
               "9) Cite a policy rule (R1-R10) only if it appears in action_reasons. "
               "10) The SAR narrative states the total suspicious amount (exposure_usd) and never talks about itself (no 'this report includes'). "
               "11) If evidence_requested is set, the summary says what was asked and what came back (assumed_response). "
               "Describe unnamed model features honestly. Do not use bullet points.")
        try:
            out = json.loads(self.chat(sys, json.dumps(bundle, default=str, ensure_ascii=False), as_json=True, max_tokens=1800 if bundle.get("sar_required") else 1100))
        except Exception:
            return None
        # Some models write IDs and dates with typographic dashes (C07297‑K1 with U+2011): make them exact ASCII,
        # so IDs match the data and the ID check below sees the whole ID.
        out = {k: v.translate(ASCII_PUNCT) if isinstance(v, str) else v for k, v in out.items()}
        text = " ".join(str(v) for v in out.values())
        bad = [i for i in ID_RE.findall(text) if i not in allowed_ids]
        self.last_rejects = [f"invented ID(s) {', '.join(sorted(set(bad))[:3])}"] if bad else []
        if bad: return None
        def n(t): return len([x for x in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", str(t).strip()) if x])
        if not 2 <= n(out.get("summary", "")) <= 6:                                    # spec: 2-6 sentences
            self.last_rejects.append(f"summary has {n(out.get('summary', ''))} sentences"); out.pop("summary", None)
        if bundle.get("sar_required") and not 6 <= n(out.get("sar_narrative", "")) <= 12:  # spec: 6-12
            self.last_rejects.append(f"SAR has {n(out.get('sar_narrative', ''))} sentences"); out.pop("sar_narrative", None)
        for key in ("summary", "sar_narrative"):   # a field that breaks a rule above falls back to the deterministic writer
            why = out.get(key) and self.breaks_rules(out[key], bundle, key)
            if why: self.last_rejects.append(f"{'SAR' if key == 'sar_narrative' else key} {why}"); out.pop(key)
        if out.get("summary") or out.get("sar_narrative"): out["writer"] = self.last_model
        return out
