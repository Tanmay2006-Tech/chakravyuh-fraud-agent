"""Tool contract shared by every graph backend.

Every method returns plain python (dicts / lists of txn dicts) in the same normalized shape,
whether it came from TigerGraph through MCP or from the offline mirror. Detectors, the policy
engine and the narrator never see backend-specific formats.

Normalized txn dict keys:
  id, ts (pd.Timestamp), amount, product, channel, risk_score, region, p_email, r_email,
  device_status, proxy, device_id, fingerprint, card_id, c1, c13, d1, dist1
"""
from abc import ABC, abstractmethod

# Graph algorithm (GDS tg_wcc) runs on the card-in-use x rare-device projection: USES_DEVICE edges link a
# Fingerprint and a DeviceProfile that transacted together online in this window, for device profiles used
# by at most WCC_MAX_LIFETIME_CARDS cards over their life. Card-level edges merge 5,103 cards into one
# uninformative component; the card-in-use is what isolates genuine rings.
WCC_FROM, WCC_TO, WCC_MAX_LIFETIME_CARDS = "2016-11-01 00:00:00", "2016-12-31 23:59:59", 80

class GraphBackend(ABC):
    name = "base"
    @abstractmethod
    def txn_context(self, txn_id): ...
    @abstractmethod
    def card_profile(self, card_id, before_ts): ...
    @abstractmethod
    def card_window(self, card_id, from_ts, to_ts): ...
    @abstractmethod
    def fingerprint_history(self, fp): ...
    @abstractmethod
    def device_neighbors(self, device_id, from_ts, to_ts): ...
    @abstractmethod
    def amount_recurrence(self, card_id, amount, tol, product, before_ts): ...
    @abstractmethod
    def card_cases(self, card_id): ...
    @abstractmethod
    def threshold_scan(self, from_ts, to_ts, lo, hi): ...
    @abstractmethod
    def shared_device_scan(self, from_ts, to_ts, min_cards, max_lifetime_cards): ...
    @abstractmethod
    def ring_component(self, device_id, max_list): ...
    @abstractmethod
    def wcc_ring_scan(self, min_devices, max_fingerprints): ...
    @abstractmethod
    def vector_search(self, kind, query_vec, k): ...
    @abstractmethod
    def write_case(self, case_vertex: dict, edges: dict, embedding=None): ...
