# Returns Agent: human-in-the-loop e-commerce returns

An agent that handles customer return requests (Arabic dialects and English) for an online store.
It **proposes** a decision; a **human approves anything that moves money**.
Built with LangGraph + FastAPI + Docker. Data is synthetic.

> Goal of this project: not "a chatbot that talks about returns", but a small,
> realistic **deployment**: policy checks in code, approval flow, security tests, and measured quality.

## How it works

```
customer message
   -> classify (LLM: defective / wrong_item / changed_mind / other, language)
   -> check_policy (plain code: order exists? belongs to customer? days since delivery, opened?)
   -> propose (LLM drafts a decision; code guardrails override it)
        |-- reject  -> fixed reply sent
        '-- approve / escalate -> human review (interrupt) -> approved / declined
```

### Design decisions (and why)

| Decision | Reason |
|---|---|
| Eligibility is computed in code, not by the LLM | Rules are deterministic and testable; the model can't be talked out of them |
| The agent has **no refund tool** | It can only propose. Worst case of a wrong proposal is a wrong item in a review queue |
| Guardrail: ineligible -> `reject`, never sent to refund approval | Found by evals (see below): the model escalated an ineligible 1500-value order |
| Refunds above a threshold always need a human | Limits financial blast radius |
| Customer messages are **fixed templates**, not LLM text | Found by evals: the model promised refunds before a human decided |
| Same generic reply for "order not found" and "not your order" | Doesn't leak whether an order exists |
| Reviewer endpoints need a token and fail closed if none is configured | Approval is the sensitive action |
| Input length caps (1000 chars) | Cost control and smaller injection surface |

## Evaluation

Two harnesses, run against the real model:

- `evals.py`: 30 labeled cases (dialects, time boundaries, opened items, high value, prompt injection, authorization).
- `evals_hard.py`: 20 harder cases x 3 runs each, plus reply-quality checks (no refund promise, same language, no data leak) and a stability check.

| Run | Decision accuracy | UNSAFE | Reply issues |
|---|---|---|---|
| `evals.py` | 30/30 | 0 | not checked |
| `evals_hard.py` before template replies | 60/60 | 0 | `promises_refund` in 2 cases (6, 13) |
| `evals_hard.py` after template replies | 60/60 | 0 | none |

UNSAFE = the case should have been rejected but the agent routed it toward a refund.

What evals found along the way:
1. First manual run: a 64-day-old opened item (value 1500) was *escalated* to a human instead of rejected. Fixed with a code guardrail.
2. `evals_hard.py`: the model wrote refund promises (once because the customer asked for it, once unprompted) while the decision was still pending. Fixed by removing the LLM from customer-facing wording.

## Run it

```bash
cp .env.example .env        # fill in GOOGLE_API_KEY and REVIEWER_TOKEN
docker compose up --build
# open http://localhost:8000/docs
```

Without Docker:
```bash
pip install -r requirements.txt
uvicorn api:app --reload
```

Endpoints:

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/returns` | customer | submit a return request |
| GET | `/returns/{id}?customer_id=...` | customer | check status |
| GET | `/reviews` | reviewer (`X-Reviewer-Token`) | pending approvals with facts + proposal |
| POST | `/reviews/{id}` | reviewer | `{"approved": true/false, "note": "..."}` |

Sample orders for trying it: `A100`/`c1` (eligible), `A101`/`c1` (too old), `B04`/`c2` (high value).

## Known limitations (honest list)

- **State is in memory.** Restarting the server loses pending reviews; run a single worker. Next step: a persistent checkpointer (SQLite/Postgres).
- **`customer_id` is trusted from the request body.** In production it must come from an authenticated session.
- **Claims can't be verified.** "The item is defective" on day 20 is accepted as a claim and routed to a human. Next step: require evidence (photo/description) before review.
- **Eval cases were written by me** against my own policy, so 100% is a floor check, not proof of production readiness. Needs real, messy customer messages.
- **The refund-promise check is a phrase list**, so it can miss paraphrases. The template approach removes the risk for pending/approved/declined replies; LLM-written rejection text is still only spot-checked.
- The model's `temperature` is ignored by the Gemini model used, so run-to-run stability can't be pinned.
- Cost and latency per request are **not measured yet**.
- Reviewer UI, rate limiting, and real store integrations (Salla/Shopify) are not implemented.