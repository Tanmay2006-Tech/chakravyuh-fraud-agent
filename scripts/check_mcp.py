"""Quick connectivity check: lists graphs, vertex counts and installed queries through MCP."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.runtime import load_env
load_env()
from agent.backends.mcp_tigergraph import _MCPClient
env = {k: v for k, v in os.environ.items() if k.startswith("TG_")}
m = _MCPClient(env)
print(json.dumps(m.call("tigergraph__list_graphs", {}), indent=1)[:800])
for vt in ["Txn", "Card", "ClosedCase", "DeviceProfile", "InvestigationCase"]:
    try: print(vt, m.call("tigergraph__get_vertex_count", {"vertex_type": vt, "graph_name": env.get("TG_GRAPHNAME", "FraudGraph")}))
    except Exception as e: print(vt, "->", str(e)[:150])
