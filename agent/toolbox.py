"""Wraps a graph backend: every call is counted and written to the investigation trace,
so the answer file's tool_calls and the UI's step log come from the same source."""
import time

def _summ(x):
    if isinstance(x, list): return f"{len(x)} rows"
    if isinstance(x, dict):
        return ", ".join(f"{k}={len(v) if isinstance(v,(list,dict)) else v}" for k, v in list(x.items())[:6])
    return str(x)[:80]

class ToolBox:
    def __init__(self, backend, embedder):
        self.b, self.emb = backend, embedder
        self.calls, self.trace, self._cache = 0, [], {}
        self.last_served = backend.name

    def reset(self):
        self.calls, self.trace = 0, []

    def call(self, tool, **args):
        key = (tool, tuple(sorted((k, str(v)) for k, v in args.items() if k != "query_vec")))
        t0 = time.time()
        if key in self._cache and tool in ("threshold_scan",):
            out = self._cache[key]; nfb = len(getattr(self.b, "fallbacks", []))
        else:
            nfb = len(getattr(self.b, "fallbacks", []))
            out = getattr(self.b, tool)(**args)
            if tool in ("threshold_scan",): self._cache[key] = out
        served = self.b.name
        if len(getattr(self.b, "fallbacks", [])) > nfb:
            served = "local-fallback"
        self.last_served = served
        self.calls += 1
        self.trace.append({"kind": "tool", "tool": tool, "backend": served,
                           "args": {k: (str(v) if not isinstance(v, (int, float, list)) else v) for k, v in args.items() if k != "query_vec"},
                           "result": _summ(out), "ms": int((time.time() - t0) * 1000)})
        return out

    def retrieve(self, kind, text, k=4):
        vec = self.emb.embed([text])[0]
        return self.call("vector_search", kind=kind, query_vec=vec, k=k)

    def note(self, step, msg, **data):
        self.trace.append({"kind": "step", "step": step, "msg": msg, **data})
