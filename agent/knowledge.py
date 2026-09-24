"""GraphRAG knowledge: the fraud policy, the documented patterns and regulatory guidance, chunked
into KnowledgeDoc vertices with embeddings. Closed-case analyst notes are embedded on ClosedCase.
The agent retrieves passages by similarity to the evidence it has gathered and passes only those
passages (not raw tables) to the LLM, and cites them as `document` evidence."""
import hashlib, os, re
import numpy as np

DIM = 384

POLICY = {
 "R1": "R1 Verify before you block on a weak signal. If the case rests on a single signal, including a risk score alone, and assessed fraud probability is below 0.70, recommend VERIFY_WITH_CUSTOMER or STEP_UP_AUTH before any block. Blocking a legitimate customer on one signal is a policy breach.",
 "R2": "R2 Customer denies the transaction. Recommend BLOCK_CARD and CREATE_CASE. Add FILE_REPORT if exposure exceeds $1,000 or the case connects to a shared device profile or another card's fraud.",
 "R3": "R3 Customer confirms the transaction. Recommend CLOSE_NO_FRAUD and note the confirmation in the case file.",
 "R4": "R4 No reply within 24 hours. Recommend MONITOR_CARD and DECLINE_TRANSACTION for pending authorizations. Escalate if exposure exceeds $500.",
 "R5": "R5 Card testing. Three or more small online authorizations on one card within an hour followed by a larger purchase: recommend DECLINE_TRANSACTION and STEP_UP_AUTH. If a purchase over $100 has already cleared, recommend BLOCK_CARD.",
 "R6": "R6 Shared origin. When several cards show fraud from the same device profile, billing region or recipient email in one window, name the shared element, recommend CREATE_CASE and FILE_REPORT, and MONITOR_CONNECTED_CARDS for every card that shares it.",
 "R7": "R7 Disputed but legitimate. When the customer disputes a charge that matches their own recurring pattern (same merchant, same amount, monthly), recommend CREATE_CASE, VERIFY_WITH_CUSTOMER and WARN_CUSTOMER. Do not block.",
 "R8": "R8 Escalate when uncertain and exposed. If the verdict is uncertain and exposure exceeds $500, or the evidence conflicts, recommend ESCALATE_TO_ANALYST.",
 "R9": "R9 Undocumented patterns. When activity fits none of the known patterns but shows coordinated or repeated abuse across customers, recommend CREATE_CASE, FILE_REPORT and ESCALATE_TO_ANALYST and describe the pattern in your own words.",
 "R10": "R10 Never BLOCK_ALL_CARDS unless at least two of the customer's cards show confirmed fraud or the customer's credentials are confirmed compromised.",
 "3a": "A case is not a report. Open a case (CREATE_CASE) whenever fraud probability reaches 0.30, whenever evidence is requested, or whenever a customer disputes a charge. File a suspicious activity report (FILE_REPORT) only when fraud is confirmed or strongly suspected and exposure exceeds $1,000, or the activity connects to a shared device, shared region cluster or another customer's fraud, or the pattern is coordinated or undocumented.",
 "3b": "The next best action can change. Recommend what the evidence supports now, request more evidence if policy calls for it, then recommend again, recording both the initial and final recommendation.",
 "routes": "Approval routes. auto: ALLOW_TRANSACTION, MONITOR_CARD, MONITOR_CONNECTED_CARDS, WARN_CUSTOMER, VERIFY_WITH_CUSTOMER, STEP_UP_AUTH, GENERATE_REPORT, CREATE_CASE, ESCALATE_TO_ANALYST, CLOSE_NO_FRAUD. L1 team lead: DECLINE_TRANSACTION; BLOCK_CARD when exposure is at most $2,500. L2 fraud manager: BLOCK_CARD above $2,500; BLOCK_ALL_CARDS; FILE_REPORT.",
 "stop": "Stopping. Stop when fraud probability is at or above 0.85 or at or below 0.15 with at least two independent pieces of evidence, when a verification response settles the question, or when further steps are unlikely to change the decision.",
}
PATTERNS = {
 "card_testing": "Card testing: a stolen card number is checked with three or more tiny online authorizations, often under $5, followed by a larger purchase. Confirmed by the sequence itself.",
 "card_not_present_fraud": "Card-not-present fraud: the card number is used online without the card, with amounts and products that do not fit the cardholder's history, often a burst of two to four within 48 hours. One unusual online purchase alone is ambiguous and should be verified.",
 "card_not_present_new_device": "Card-not-present fraud from a new device: as card-not-present fraud, with the identity record marking the device as New for this account, sometimes behind a proxy. Stronger than plain CNP but not proof, since people buy new phones.",
 "out_of_region_use": "Out-of-region use: card-present purchases in a billing region the cardholder has no history in while normal activity continues at home. Several days of purchases in one new region is a trip, not a clone.",
 "account_takeover": "Account takeover: mixed-channel activity inconsistent with the cardholder, often with device and match-flag anomalies, pointing to stolen credentials rather than a stolen number.",
}
REGULATORY = {
 "fincen-sar-narrative": "FinCEN SAR narrative guidance: the narrative must answer who is conducting the activity, what instruments and amounts are involved, when it occurred, where, how it was carried out and why it is suspicious, in a self-contained chronological account.",
 "fincen-ato-advisory": "FinCEN account takeover advisory (FIN-2011-A016): red flags include logins from new devices or anonymising proxies, changes to account details followed by unusual transactions, and rapid successive transactions inconsistent with history.",
 "fincen-identity": "FinCEN identity-related suspicious activity (2021): compromised credentials and synthetic identities are leading drivers; device and IP linkages across unrelated customers are key indicators of organised activity.",
 "ffiec-red-flags": "FFIEC red flags: transactions structured to stay under reporting or authorization thresholds, repeated round-dollar amounts, and activity inconsistent with the customer's profile warrant review and possibly a SAR.",
 "fatf-cyber-fraud": "FATF cyber-enabled fraud: organised groups reuse infrastructure such as devices, mule accounts and email domains across many victims; linking victims through shared infrastructure is central to detection.",
}

def all_docs():
    docs = [("policy:" + k, "policy", f"Fraud Policy {k}", v) for k, v in POLICY.items()]
    docs += [("pattern:" + k, "pattern", k, v) for k, v in PATTERNS.items()]
    docs += [("reg:" + k, "regulatory", k, v) for k, v in REGULATORY.items()]
    return docs

class Embedder:
    """bge-small (384-d) through fastembed when installed; otherwise a deterministic hashed
    bag-of-words fallback so the pipeline still runs fully offline."""
    def __init__(self, prefer="fastembed"):
        self.model = None
        pinned = os.path.join("data", "vectors", "embedder.txt")     # written by setup: query vectors must match stored ones
        if os.path.exists(pinned) and open(pinned).read().strip() == "hashed-bow":
            prefer = "hashed"
        if prefer == "fastembed":
            try:
                from fastembed import TextEmbedding
                self.model = TextEmbedding("BAAI/bge-small-en-v1.5")
            except Exception:
                self.model = None
        self.kind = "bge-small-en-v1.5" if self.model else "hashed-bow"

    def embed(self, texts):
        if self.model:
            return np.array(list(self.model.embed(list(texts))), dtype=np.float32)
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            toks = re.findall(r"[a-z0-9$]+", t.lower())
            for a, b in zip(toks, toks[1:] + [""]):
                for g in (a, a + "_" + b):
                    h = int(hashlib.md5(g.encode()).hexdigest(), 16)
                    out[i, h % DIM] += 1.0 if (h >> 8) % 2 else -1.0
            n = np.linalg.norm(out[i]); out[i] /= (n or 1)
        return out
