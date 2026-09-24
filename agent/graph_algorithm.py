"""Graph-algorithm evidence: TigerGraph's GDS weakly connected components (tg_wcc).

tg_wcc runs over the card-in-use x rare-device projection (USES_DEVICE, see backends/base.py) and stores
each vertex's component in wcc_id; the installed query ring_component reads the component of the flagged
payment's device. The result is added to the case as evidence with weight 0.0: it is shown and explained,
but it cannot move the probability, the verdict or the actions, which come from the calibrated detectors.
The node runs after the decision and the explanation, so it cannot change retrieval queries or the LLM input.
"""
from .detectors import Signal

MAX_LIST = 400          # ring_component returns member IDs only for components up to this many fingerprints
RING_MAX_CARDS = 80     # a component with more cards than this is a loose cluster, not one ring

def card_of(fp):
    """Fingerprint IDs are 'F:<card_id>|<region>|<account day>|<email domain>'."""
    return fp.split("|")[0][2:] if fp.startswith("F:") else fp.split("|")[0]

def component_signal(tb, ctx):
    """Returns a weight-0 Signal describing the flagged device's tg_wcc component, or None."""
    if ctx.get("channel") != "online" or not ctx.get("device_id"): return None
    r = tb.call("ring_component", device_id=ctx["device_id"], max_list=MAX_LIST)
    n_fp, n_dev = r.get("n_fingerprints") or 0, r.get("n_devices") or 0
    if n_fp == 0: return None                       # device not rare, or not active in the window: no ring to speak of
    cards = sorted({card_of(f) for f in r.get("fingerprints") or []})
    ref = "query:ring_component (GDS tg_wcc over USES_DEVICE)"
    if r.get("fingerprints") and len(cards) <= RING_MAX_CARDS:
        if len(cards) < 2: return None
        others = [c for c in cards if c != ctx["card_id"]]
        claim = (f"TigerGraph graph algorithm (GDS weakly connected components, tg_wcc) over the Nov–Dec graph of cards-in-use and rare "
                 f"device profiles puts this payment's device in a connected component of {len(cards)} cards ({n_fp} card fingerprints, "
                 f"{n_dev} device profile{'s' if n_dev != 1 else ''}). Context only: it does not change the fraud probability")
        return Signal("graph_algorithm", 0.0, claim, ref, [ctx["card_id"]] + others[:9])
    claim = (f"TigerGraph graph algorithm (GDS weakly connected components, tg_wcc) puts this payment's device in a large, loosely connected "
             f"cluster ({n_fp:,} card fingerprints across {n_dev:,} rare device profiles): too large to read as a single ring. "
             f"Context only: it does not change the fraud probability")
    return Signal("graph_algorithm", 0.0, claim, ref, [ctx["card_id"]])
