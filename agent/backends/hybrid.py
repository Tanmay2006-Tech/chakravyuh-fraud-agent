"""TigerGraph-first backend with a safety net. Every tool call goes to TigerGraph through MCP;
if one call fails (a query not installed, a timeout, a workspace waking from auto-stop), that
single call is served by the offline mirror and the trace records it, so a 20-case run always
completes and you can see exactly which calls were not served by the graph.
Use --strict to disable the fallback."""
class HybridBackend:
    name = "tigergraph-mcp"
    def __init__(self, primary, fallback, strict=False):
        self.p, self.f, self.strict = primary, fallback, strict
        self.fallbacks = []
    def __getattr__(self, tool):
        if tool.startswith("_") or tool in ("p", "f", "strict", "fallbacks"): raise AttributeError(tool)
        def call(**kw):
            try:
                out = getattr(self.p, tool)(**kw)
                if tool == "write_case": self.f.write_case(**kw)     # keep the local UI memory view in sync
                return out
            except Exception as e:
                if self.strict: raise
                self.fallbacks.append((tool, str(e)[:300]))
                self.last_served = "local-fallback"
                return getattr(self.f, tool)(**kw)
        return call
