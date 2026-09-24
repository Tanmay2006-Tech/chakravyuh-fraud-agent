"""Customer / analyst responses are not provided in this round (README section 5). We simulate them
deterministically from the evidence, so the assumption is explicit and reproducible:
  customer validation: probability >= 0.65 -> denies; <= 0.35 -> confirms; otherwise -> no reply in 24h
  recurring-charge dispute (R7) -> customer recognises the recurring charge
  analyst info on a ring -> analyst confirms linked cards show the same activity (only when the graph already shows it)"""

def customer_validation(p, r7=False, ctx=None):
    if r7:
        return "confirm", ("Customer, shown the earlier identical charges, recognises it as their own recurring purchase and withdraws the dispute")
    if p >= 0.65:
        return "deny", "Customer states they did not make the transaction and still has the card"
    if p <= 0.35:
        return "confirm", "Customer confirms they made the transaction" + (" from a new device" if ctx and ctx.get("device_status") == "New" else "")
    return "no_reply", "No reply from the customer within 24 hours"

def analyst_ring(n_cards, element):
    return "confirm", f"Analyst confirms {element} links {n_cards} other cards with the same activity in the window and agrees it is one coordinated scheme"
