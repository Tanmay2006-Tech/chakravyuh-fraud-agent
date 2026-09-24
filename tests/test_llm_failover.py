"""LLM failover and text guards, with fake provider clients (no network, no keys).
Covers: primary success; 429/503 retried on the same provider (Retry-After honoured); failover; hard errors;
the 4-failure cooldown and its expiry; token counting across providers; template fallback when all fail;
the legacy LLM_MODEL override; and ASCII normalisation of typographic hyphens in IDs.
   python -m tests.test_llm_failover"""
import os, sys, json, types, time, httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(LLM_PROVIDER="groq", GROQ_API_KEY="x", GEMINI_API_KEY="y", GROQ_MODEL="g-model", GEMINI_MODEL="m-model"); os.environ.pop("LLM_MODEL", None)
from openai import APIStatusError, APIConnectionError
import agent.llm as M
slept = []; M._sleep = slept.append
req = httpx.Request("POST", "http://x")
def err(code, headers=None, body=None): return APIStatusError("boom", response=httpx.Response(code, request=req, headers=headers or {}), body=body)
class Fake:
    def __init__(self, script): self.script = list(script); self.calls = 0
    chat = completions = property(lambda self: self)
    def create(self, **kw):
        self.calls += 1; a = self.script.pop(0)
        if isinstance(a, Exception): raise a
        return types.SimpleNamespace(usage=types.SimpleNamespace(total_tokens=10), choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=a))])
ok = json.dumps({"summary": "Card was used. It was fine."})
def fresh(g, m):
    l = M.LLM(); l.providers[0]["client"], l.providers[1]["client"] = Fake(g), Fake(m); slept.clear(); return l

l = fresh([ok], []); assert l.write({}, set())["writer"] == "g-model"                                   # primary works
l = fresh([err(503), ok], []); assert l.write({}, set())["writer"] == "g-model" and slept == M.BACKOFF[:1] and l.retries == 1 and l.failovers == 0
l = fresh([err(429, {"retry-after": "3"}), ok], []); l.write({}, set()); assert slept == [3.0]            # Retry-After respected
l = fresh([err(429)] * 3, [ok]); assert l.write({}, set())["writer"] == "m-model"                       # 2 retries, then failover
assert slept == M.BACKOFF and l.retries == 2 and l.failovers == 1 and l.providers[0]["fails"] == 1 and not l.providers[0]["until"]
l = fresh([err(404)], [ok, ok]); assert l.write({}, set())["writer"] == "m-model" and slept == [] and l.providers[0]["dead"]
g = l.providers[0]["client"]; l.write({}, set()); assert g.calls == 1                                    # hard error: disabled for run
l = fresh([err(400, body={"code": "json_validate_failed"})], [ok]); assert l.write({}, set())["writer"] == "m-model"
assert not l.providers[0]["dead"] and slept == []                                                       # JSON-validation 400 is not hard
l = fresh([err(500)] * 4 + [ok], [ok] * 5)
for _ in range(4): assert l.write({}, set())["writer"] == "m-model"
assert l.providers[0]["until"] > time.time() and not l.providers[0]["dead"]                             # 4 in a row -> cooldown
g = l.providers[0]["client"]; n = g.calls; l.write({}, set()); assert g.calls == n                      # skipped while cooling
l.providers[0]["until"] = time.time() - 1; assert l.write({}, set())["writer"] == "g-model"             # re-enabled after cooldown
assert l.providers[0]["fails"] == 0 and l.tokens == 60                                                   # tokens across both
l = fresh([APIConnectionError(request=req)], [err(500)]); assert l.write({}, set()) is None and l.plan({}, []) is None  # template
os.environ["LLM_MODEL"] = "legacy"; assert M.LLM().providers[0]["model"] == "legacy" and M.LLM().providers[1]["model"] == "m-model"
os.environ["GROQ_BACKUP_MODEL"] = "g-small"                                                              # optional second Groq model
lb = M.LLM(); assert [p["name"] for p in lb.providers] == ["groq", "groq-backup", "gemini"]
lb.providers[0]["client"], lb.providers[1]["client"], lb.providers[2]["client"] = Fake([err(429)] * 3), Fake([ok]), Fake([ok]); slept.clear()
assert lb.write({}, set())["writer"] == "g-small" and lb.failovers == 1                                 # rate-limited primary -> backup model
os.environ.pop("GROQ_BACKUP_MODEL")
os.environ.pop("GEMINI_API_KEY"); assert [p["name"] for p in M.LLM().providers] == ["groq"]
os.environ.pop("GROQ_API_KEY"); assert not M.LLM().on and M.LLM().name == "template (no LLM key set)"
print("failover test: PASS")
os.environ.update(GROQ_API_KEY="x", GEMINI_API_KEY="y"); os.environ.pop("LLM_MODEL")
nb = json.dumps({"summary": "Card C07297\u2011K1 was used on 2016\u201111\u201121. It was fine \u2014 really."})
l = fresh([nb], []); r = l.write({}, {"C07297-K1"}); assert r["summary"] == "Card C07297-K1 was used on 2016-11-21. It was fine \u2014 really.", r
l = fresh([nb], []); assert l.write({}, {"C07297"}) is None        # invented card suffix is now caught by the ID guard
print("hyphen normalisation test: PASS")
print("RESULT: PASS")
