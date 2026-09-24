# Building Chakravyuh: an agentic fraud investigator on TigerGraph

*Tanmay Tripathi · Hacker House Goa 2026 · TigerGraph Agentic Fraud Investigation challenge*

Fraud analysts rarely lose to a lack of data. They lose to the time it takes to join it: pull the card's history, trace the device, check whether the same number turned up in an old case, read the policy, decide, document. By the time the case is written up, the money has moved. The Hacker House Goa 2026 TigerGraph challenge asked for an agent that does that whole loop — and knows when it does not yet know enough to act. This is how I built **Chakravyuh**, named after the spiral formation of the Mahabharata that surrounds whoever enters it ring by ring — which is how it investigates: from the flagged transaction outward to the card, the real cardholder, the device, the other cards that device touched, and the bank's past cases.

## What it does

Given an alert from the case pack (a model score, a customer complaint or an analyst request), Chakravyuh:

1. opens an investigation on the flagged transaction and pulls its context from the graph;
2. gathers evidence with installed GSQL queries called through the TigerGraph MCP server;
3. recalls memory — closed cases on the same card fingerprint, device and card, plus semantically similar closed-case narratives through TigerVector;
4. when the evidence links other cards, lets the LLM choose follow-up graph calls (for example, checking whether cards linked by a shared device already have fraud cases);
5. turns evidence into a calibrated fraud probability, a pattern and the full fraud episode;
6. recommends an initial next best action under the bank's policy, with the approval route for each action;
7. when the signal is weak, requests evidence through a policy-approved channel (step-up authentication, customer validation, analyst information) and re-decides;
8. writes the case summary and, when section 3a requires it, a stand-alone suspicious activity report;
9. writes the finished case back into TigerGraph as an `InvestigationCase` vertex linked to its card, transactions, device, pattern, connected cards and the prior cases it used.

On the 20 benchmark cases it reached 9 fraud, 10 legitimate and 1 uncertain verdict, filed 4 reports, requested evidence in 12 cases (5 customer validations, 3 step-up challenges, 4 analyst confirmations) and changed its recommendation after the evidence in all 12.

## Reading the data before writing the agent

The most valuable hours went into the data, not the agent.

**Card IDs are not in the transaction file.** Cases name cards like `C05876-K2`, but `transactions.csv` only has `customer_id`. Grouping the customer's transactions by different card fields and testing each ordering against the closed cases found the rule: the K-number is the rank of the `card6` value within the customer, with blanks first. It reproduces every one of the 5,565 closed-case card IDs. Without it, every connected card ID in an answer would be unverifiable.

**A "customer" is many people.** `customer_id` is derived from an issuer field, so one customer can carry thousands of transactions across dozens of billing regions. "Has this customer ever shopped in region 330?" is almost always yes and means almost nothing. So Chakravyuh derives a **card fingerprint**: card + billing region + account-start day (transaction day minus `D1`) + purchaser email domain. In the closed history, confirmed-fraud and cleared fingerprints essentially never overlap. That turned case memory from "similar-sounding cases" into "this exact card-in-use has been confirmed stolen three times before".

**Some schemes only show up across customers.** Two undocumented patterns surfaced in the closed cases and in the exam window: a Samsung SM-G935F profile behind an anonymous proxy, marked New on every use, hitting 52 customers; and runs of four online purchases just under $500 within about half an hour, on 12 different cards in November–December.

**A device profile is not a device.** My first shared-device detector lit up hundreds of "rings": a profile is a model/OS/browser string, and popular phones are shared by definition. The fix was coherence — a rare profile only counts as a shared origin when the other cards' activity matches this one (same email domain, amount within 10%), or when it sits behind an anonymous proxy and is New on almost every use.

Rings also have to beat the cardholder's own habits. In HHG-011 a customer disputes $131.30, and the card has paid about $131 for the same product several times since July, which at first sight is policy R7's "disputed but legitimate" recurring charge. It isn't: those earlier payments came from different devices and different email domains, so they are not one cardholder's subscription. The disputed payment came from a rare phone profile that paid $125–$131 on three other cards within three days, all with the same email domain, and one of those cards already has a confirmed-fraud case. Chakravyuh treats it as a shared-origin ring: block, report, and watch the linked cards.

## Memory overturned my priors

My first scoring model treated a New device, an out-of-pattern amount and an anonymous email domain as fraud signals, because that is what the pattern descriptions suggest. Then I replayed the bank's own history through the agent — each closed case as a fresh alert, seeing only the cases opened before it — and the pre-evidence probability had an AUC of 0.45 on 400 cases from mid-September to October. Worse than a coin.

The history explained why. Every one of the 900 model-scored alerts in the closed cases was cleared (716 travel, 158 new phones, 26 unusual-but-genuine amounts), and every customer-reported case was confirmed fraud. Among 1,010 historical alerts with a score of 0.5 or more, only 8% of those on a New device were fraud, against 63% of the rest. Conditional on the model having already fired, a new device is the model's own explanation — usually a new phone — not extra evidence. Statisticians call it explaining away.

So `scripts/calibrate.py` learns a log-likelihood-ratio weight per detector from July to mid-September, and for model-score alerts the agent blends each prior weight 50/50 with the learned one and quotes the history in the evidence ("bank memory: 8% of 577 past model alerts with this signal were fraud"). Customer reports keep policy weights, because history offers no counter-examples. On that same later window, which the calibration never saw, the AUC rose from 0.45 to 0.66 and accuracy on decided cases from 49% to 66% (`docs/backtest*.json`). The remaining gap is honest: a customer-reported fraud replayed as a model alert has lost its most important evidence, the customer.

## How TigerGraph is used

**Graph.** 11 vertex types (`Customer`, `Card`, `Fingerprint`, `Txn`, `DeviceProfile`, `EmailDomain`, `BillingRegion`, `FraudPattern`, `ClosedCase`, `InvestigationCase`, `KnowledgeDoc`) and 13 edge types, with reverse edges so every query can walk both ways. `ClosedCase`, `InvestigationCase` and `KnowledgeDoc` carry 384-dimension embeddings (bge-small) for TigerVector search.

**Queries.** Thirteen installed GSQL queries: transaction context; card behavioural profile (with distinct days per region via accumulators); card window; fingerprint history with the closed cases that touched it; device neighbours over a window and a lifetime; amount recurrence for disputes; case memory by card; a portfolio threshold scan; a shared-device scan that keeps only rare profiles touching several cards; two TigerVector searches (policy knowledge and closed-case narratives); and two readers of the graph-algorithm result.

**Graph algorithm.** The GDS library's weakly connected components (`tg_wcc`, installed unmodified) runs on a projection built in GSQL: an undirected `USES_DEVICE` edge between a card fingerprint and a *rare* device profile (at most 80 cards over its life) whenever they transacted together online in November–December. The choice of node matters. My first projection linked whole cards to devices, and WCC merged 5,103 cards into one component, because a "card" in this dataset is shared by many people and chains everything together. Linking the card-in-use instead isolates real rings: HHG-014's proxy device sits in a component of 28 cards, HHG-019's in one of 5, and HHG-011's device lands in a loose 3,554-fingerprint cluster, which the agent labels as too large to be a ring rather than pretending otherwise. The component is added to every case as evidence with weight 0: it explains, it never scores, and a test proves every probability, action and report is identical without it. In the monitor, the same result lists 85 rings that tie three or more cards to several rare devices, which the single-device scan cannot see.

**MCP.** The agent never talks to TigerGraph directly. Every read is `tigergraph__run_installed_query`; the case vertex is created with `tigergraph__add_node`, linked to its card, transactions, device, pattern, connected cards and prior cases with `tigergraph__add_edges`, and embedded with `tigergraph__upsert_vectors`. Even setup — schema, vector attributes, loading jobs, data, embeddings, query installation — runs through the MCP server.

## Architecture

**Agent.** A LangGraph state machine: intake → gather evidence → recall memory → follow-up → assess → initial action → (request evidence) → final action → explain → write memory. The request node is conditional: decisive cases skip it, which is the stopping rule in code.

**Scoring.** Each detector emits a signal with a fixed log-odds weight and the entity IDs it rests on: fingerprint with confirmed-fraud history (+2.8), threshold structuring (+3.0), shared origin (+2.5), card testing (+2.5), region novelty (+1.3), amount anomaly (+0.7), new device (+0.6); and on the legitimate side established fingerprint (−1.5), habitual amount (−1.2), recurring disputed charge (−1.8), routine region (−0.8), a device whose past alerts were cleared (−0.7). The model's risk score is deliberately weak (+1.2 × (score − 0.5)), because the README says above 0.7 most alerts are legitimate. Independent evidence is counted by signal family, which is what the stopping rule needs.

**Policy as code.** R1–R10, section 3a and the routing table are implemented literally. The engine distinguishes a case from a report, routes `BLOCK_CARD` to L1 or L2 by exposure, and never blocks on a single signal below 0.70.

**LLM.** Groq's `openai/gpt-oss-120b` is the primary model and Gemini `gemini-3.6-flash` the backup. A rate limit or overload is retried on the same provider with backoff, then fails over to the other; a provider that keeps failing is cooled down for 90 seconds, and if both are down the deterministic writer takes over. Each trace records the model that actually wrote that case; in the committed run `gpt-oss-120b` wrote 7 cases and then hit its daily token cap, and the failover handed the other 13 to `gpt-oss-20b` without stopping the run, which is exactly the situation it exists for. Two SAR narratives (HHG-006, HHG-014) left out required subject IDs, so the guard used the deterministic writer for those.

**Evidence sources.** The graph supplies transaction history, device and identity records, account behaviour and the bank's closed cases. The bank's model score is treated as an external signal, deliberately weak. Customer and analyst replies are not in the dataset, so they are simulated explicitly (below), and every assumption is written into the answer file.

**GraphRAG.** Policy rules, the five patterns and short regulatory summaries (FinCEN SAR narrative guidance, the account-takeover advisory, FFIEC red flags, FATF cyber-enabled fraud) are `KnowledgeDoc` vertices. The agent retrieves passages by similarity to its evidence and cites them as `document` evidence; the LLM only ever sees the evidence bundle and those passages, never raw tables.

## The agentic part: acting under uncertainty

Three cases show the range.

**HHG-001** — model score 0.61 on a $77 in-person purchase. The fingerprint has ten earlier purchases in the same region since September, including $76.94 and $77.08 on the two previous Saturdays. Probability 0.04 on two independent legitimate signals: allow and close, no case, no customer friction.

**HHG-010** — score 0.90 on a $1,000.03 online purchase from a New device with an anonymous email. It looks like the textbook alert, and it is exactly what the bank's memory says is usually a customer with a new phone: probability 0.13 before asking. Because $1,000.03 is too much to wave through without contact, the agent still asks the customer before closing, and the assumed confirmation closes the case. No block, no report, no angry customer.

**HHG-006** — a customer disputes $482.12. The graph shows four purchases between $456 and $489 in 30 minutes from two devices, and the same run shape on eleven other cards within 30 days either side. It is not one of the five patterns, so Chakravyuh calls it undocumented, describes it in its own words, files, escalates and monitors every linked card.

Customer and analyst replies are not provided, so the simulator makes the assumption explicit and deterministic: at probability ≥ 0.65 the customer denies, at ≤ 0.35 they confirm, in between they do not reply within 24 hours — and R4 applies. One case (HHG-002) ends honestly uncertain, under monitoring.

## Controls and a living case record

The agent never acts on the outside world directly. Every recommendation goes through an action gateway that reads the policy's approval routes: `auto` actions (verify with the customer, step-up authentication, monitor, open a case, escalate) are executed through mock APIs and receipted; anything that declines a payment, blocks a card or reports a customer is queued for a team lead (L1) or a fraud manager (L2). The case itself is written to TigerGraph as soon as it opens and updated when it closes, with a timeline of every stage — opened, evidence added, memory recalled, assessed, initial recommendation, evidence requested, response, final recommendation, closed or escalated. Because those vertices are linked to cards, transactions and devices, the agent's own investigations become memory too: when the monitoring sweep later investigates another card in the under-$500 scheme, its evidence includes "an earlier Chakravyuh investigation, INV-HHG-006, already linked this card to fraud".

## Planning for every answer

When the agent asks a customer something, it does not wait to find out what it should do next. Before asking, it runs the policy engine once for each possible reply — denial, confirmation, silence — and records the plan. On HHG-010 that plan reads: if the customer denies it, block the card and file a report (the amount is just over $1,000); if they confirm it, let it through; if they say nothing, decline pending payments, keep watching and hand it to an analyst. A confirmation that contradicts strong graph evidence (a fingerprint with three confirmed frauds, say) is not taken at face value: the plan escalates under R8 instead of closing.

## The console

The analyst console is built around the name. Each case is drawn as a chakravyuh: the payment in the centre and four rings around it — the payment itself, the cardholder's own habits, the device and other cards, and the bank's past cases — each coloured by where its evidence points. Around that sit a plain-language verdict ("Block it and protect the customer"), a fraud-chance bar showing the probability before and after asking, the recommended actions with who must approve them, and an approval queue where team leads and fraud managers approve or reject the actions the agent is not allowed to take alone. Every case has its own link.

## What I learned

* Reverse-engineering identity was worth more than any model. A graph is only as good as its keys.
* Graph signals need a notion of "rare". Degree alone flags every popular phone.
* Memory must be scoped. My first version counted a fraudster's repeated purchases as the cardholder's "recurring habit". Legitimate-behaviour signals are now switched off when the fingerprint has confirmed fraud.
* Test against history before trusting intuition. The backtest found the biggest error in the whole system in under a minute.
* Keep the LLM on language, and check its language. Deterministic scoring made the 20 answers reproducible and auditable; the LLM makes them readable. My first LLM summaries were fluent and subtly wrong: one SAR called a device's history "suspicious" when its past alerts had all been cleared, and "±15 days" became "the preceding 15 days". Now the LLM is told which way each finding points, and every rule it can break (an invented ID, a one-sided window, an analyst who was never asked, a SAR subject it forgot to name) sends that field back to the deterministic writer, with the reason in the trace.

## What I would improve with more time

* Replace the 50/50 blend with a proper hierarchical model of detector weights by trigger type, and plot calibration.
* Let the WCC component carry weight once it is calibrated on the closed cases, and try Louvain community detection to split the large loose clusters.
* Use `Txn → NEXT → Txn` edges for sequence queries directly in GSQL.
* Real step-up and customer-messaging integrations instead of simulated replies.
* An analyst feedback loop: approvals and rejections in the console written back as case outcomes.

*Code: github.com/Tanmay2006-Tech/chakravyuh-fraud-agent*
