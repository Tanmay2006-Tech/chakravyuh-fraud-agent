# LinkedIn / X post

Built **Chakravyuh**, an agentic fraud investigator, for Hacker House Goa 2026 (TigerGraph Agentic Fraud Investigation challenge) 🌀

Give it an alert and it runs the investigation an analyst would: walks the transaction graph through TigerGraph MCP, recalls the bank's past cases, works out what kind of fraud it is and how far it goes, asks for more evidence when the signal is weak, recommends actions with the right approval level, files the SAR when policy requires it — and writes the case back into the graph so the next investigation remembers it.

Three things I learned:
🔑 The card IDs in the cases weren't in the transaction data. Reverse-engineering them, plus a "card fingerprint" for who is really using a card, mattered more than any model.
🕸️ A device profile is a phone model, not a phone. Shared-device signals only mean something when the linked activity is coherent.
🧠 Memory beats intuition. Replaying the bank's closed cases showed that 92% of past model alerts on a "new device" were not fraud: usually just a new phone. Calibrating on that history lifted out-of-time AUC from 0.45 to 0.66.

The agent also found a scheme nobody asked about: four purchases just under $500 in 30 minutes, repeated across eleven cards. And TigerGraph's connected-components algorithm, run on the card-in-use rather than the card, surfaced 85 device rings a simple shared-device check can't see.

Blog: <link> · Demo: <link> · Code: github.com/Tanmay2006-Tech/chakravyuh-fraud-agent

@TigerGraphDB #TigerGraph #GraphRAG #AgenticAI #FraudDetection #HackerHouseGoa2026 #HHGOA
