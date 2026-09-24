"""The bank's Fraud Policy v1.0 as code: rules R1-R10, section 3a (case vs report),
approval routing, and the stopping rule. Every recommended action carries its route and rule."""

AUTO = {"ALLOW_TRANSACTION","MONITOR_CARD","MONITOR_CONNECTED_CARDS","WARN_CUSTOMER","VERIFY_WITH_CUSTOMER","STEP_UP_AUTH",
        "GENERATE_REPORT","CREATE_CASE","ESCALATE_TO_ANALYST","CLOSE_NO_FRAUD"}

def route(action, exposure):
    if action in AUTO: return "auto"
    if action == "DECLINE_TRANSACTION": return "L1"
    if action == "BLOCK_CARD": return "L1" if exposure <= 2500 else "L2"
    return "L2"   # BLOCK_ALL_CARDS, FILE_REPORT

def A(action, exposure, reason):
    return {"action": action, "route": route(action, exposure), "reason": reason}

def sar_needed(verdict, exposure, shared, undocumented):
    """Section 3a: fraud confirmed/strongly suspected AND (exposure > $1,000 OR shared element OR coordinated/undocumented)."""
    if verdict != "fraud": return False, "No report: activity is not confirmed or strongly suspected fraud (section 3a)."
    why = []
    if exposure > 1000: why.append(f"exposure ${exposure:,.2f} exceeds $1,000")
    if shared: why.append("the activity connects to a shared device profile / other customers' cards")
    if undocumented: why.append("the pattern is coordinated and undocumented (R9)")
    if why: return True, "File: fraud strongly suspected and " + "; ".join(why) + " (section 3a, R2/R6/R9)."
    return False, f"Case only, no report: exposure ${exposure:,.2f} is under $1,000 and nothing links it to a shared device or another customer's fraud (section 3a)."

def fraud_actions(trigger, exposure, flags, denial_rule):
    """Actions once fraud is established (customer denial or decisive evidence)."""
    acts = []
    if flags.get("card_testing"):
        acts.append(A("DECLINE_TRANSACTION", exposure, "R5: testing sequence observed"))
        acts.append(A("BLOCK_CARD", exposure, "R5: a purchase over $100 already cleared" if exposure > 100 else "R5/R2: card number compromised"))
    else:
        if trigger != "customer_report":
            acts.append(A("DECLINE_TRANSACTION", exposure, f"{denial_rule}: stop pending authorizations on the compromised card"))
        acts.append(A("BLOCK_CARD", exposure, f"{denial_rule}: card compromised; exposure ${exposure:,.2f} {'≤' if exposure <= 2500 else '>'} $2,500"))
    acts.append(A("CREATE_CASE", exposure, f"{denial_rule} / section 3a: fraud case with evidence written to the graph"))
    return acts
