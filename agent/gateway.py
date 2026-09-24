"""Action gateway: the only way the agent touches the outside world.
Permissions come from the policy's approval routes: the agent may EXECUTE only `auto` actions
(through mock/stub APIs); `L1` and `L2` actions are QUEUED for a team lead or fraud manager.
Every dispatch returns a receipt that goes into the case timeline."""
import hashlib

MOCK_API = {
    "VERIFY_WITH_CUSTOMER": "customer-messaging API (mock): verification message sent to the cardholder",
    "STEP_UP_AUTH": "authentication API (mock): one-time passcode challenge issued",
    "WARN_CUSTOMER": "customer-messaging API (mock): warning sent to the cardholder",
    "MONITOR_CARD": "monitoring API (mock): card added to the watchlist",
    "MONITOR_CONNECTED_CARDS": "monitoring API (mock): linked cards added to the watchlist",
    "CREATE_CASE": "case management: fraud case recorded on the InvestigationCase vertex in TigerGraph",
    "ESCALATE_TO_ANALYST": "analyst queue (mock): case assigned to a fraud analyst",
    "ALLOW_TRANSACTION": "authorization API (mock): payment released",
    "CLOSE_NO_FRAUD": "case management: investigation closed as not fraud",
    "GENERATE_REPORT": "reporting (mock): internal report generated",
}
APPROVER = {"L1": "team lead", "L2": "fraud manager"}

def dispatch(case_id, actions, phase, extra=None):
    out = []
    for a in actions:
        rid = "R-" + hashlib.md5(f"{case_id}:{phase}:{a['action']}".encode()).hexdigest()[:8]
        if a["route"] == "auto":
            detail = MOCK_API.get(a["action"], "executed")
            if a["action"] == "MONITOR_CONNECTED_CARDS" and extra: detail += f" ({extra} cards)"
            out.append({"action": a["action"], "phase": phase, "status": "executed", "receipt": rid, "detail": detail})
        else:
            out.append({"action": a["action"], "phase": phase, "status": "queued_for_approval", "receipt": rid,
                        "detail": f"waiting for {APPROVER[a['route']]} approval ({a['route']}); the agent is not permitted to do this itself"})
    return out
