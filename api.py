"""
Returns Agent API (Day 3)

Run locally:   uvicorn api:app --reload
Docs (try it): http://127.0.0.1:8000/docs

Env vars:
  GOOGLE_API_KEY   key for the LLM provider (see returns_agent.py)
  MODEL            optional model name
  REVIEWER_TOKEN   secret for the human-review endpoints (required)

Known limits (also listed in README):
  - State lives in memory (MemorySaver + REQUESTS dict): restart = lost pending reviews.
    Run ONE worker only.
  - customer_id comes from the request body. In production it must come from
    an authenticated session, never from the client.
"""
import logging
import os
import secrets
import uuid

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from langgraph.types import Command

from returns_agent import app as graph

log = logging.getLogger("returns_api")
logging.basicConfig(level=logging.INFO)

api = FastAPI(title="Returns Agent", version="0.1.0")
app = api  # so both `uvicorn api:app` and `api:api` work

REQUESTS: dict[str, dict] = {}  # request_id -> record (in memory, demo only)


# ---------- Schemas ----------
class ReturnRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=40)
    customer_id: str = Field(min_length=1, max_length=40)
    message: str = Field(min_length=1, max_length=1000)  # cap size: cost + injection surface


class ReviewDecision(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=500)


# ---------- Helpers ----------
def require_reviewer(token: str | None):
    expected = os.getenv("REVIEWER_TOKEN")
    if not expected:
        # Fail closed: never run review endpoints without a configured secret
        raise HTTPException(503, "Reviewer access is not configured")
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(401, "Unauthorized")


def cfg(request_id: str) -> dict:
    return {"configurable": {"thread_id": request_id}}


# ---------- Customer-facing ----------
@api.get("/health")
def health():
    return {"ok": True}


@api.post("/returns")
def create_return(req: ReturnRequest):
    rid = uuid.uuid4().hex[:12]
    try:
        out = graph.invoke(
            {"order_id": req.order_id, "customer_id": req.customer_id, "message": req.message},
            cfg(rid),
        )
    except Exception:
        log.exception("agent failed for request %s", rid)
        raise HTTPException(502, "Could not process the request right now. Please try again.")

    if "__interrupt__" in out:
        info = out["__interrupt__"][0].value
        REQUESTS[rid] = {
            "status": "pending_review",
            "customer_id": req.customer_id,
            "customer_message": req.message,
            "facts": info["facts"],
            "proposal": info["proposal"],
            "reply": info["proposal"]["reply_to_customer"],
        }
    else:
        res = out["result"]
        REQUESTS[rid] = {
            "status": res["status"],
            "customer_id": req.customer_id,
            "customer_message": req.message,
            "proposal": out.get("proposal"),
            "reply": res["reply"],
        }
    log.info("request %s -> %s", rid, REQUESTS[rid]["status"])
    r = REQUESTS[rid]
    return {"request_id": rid, "status": r["status"], "reply": r["reply"]}


@api.get("/returns/{request_id}")
def get_return(request_id: str, customer_id: str):
    r = REQUESTS.get(request_id)
    # Same 404 for "missing" and "someone else's" so ids can't be probed
    if not r or r["customer_id"] != customer_id:
        raise HTTPException(404, "Not found")
    return {"request_id": request_id, "status": r["status"], "reply": r["reply"]}


# ---------- Reviewer-facing (token protected) ----------
@api.get("/reviews")
def list_reviews(x_reviewer_token: str | None = Header(default=None)):
    require_reviewer(x_reviewer_token)
    return [
        {
            "request_id": rid,
            "customer_message": r["customer_message"],  # untrusted text: display as plain text
            "facts": r["facts"],
            "proposal": {
                "decision": r["proposal"]["decision"],
                "rationale": r["proposal"]["rationale"],
            },
        }
        for rid, r in REQUESTS.items()
        if r["status"] == "pending_review"
    ]


@api.post("/reviews/{request_id}")
def decide_review(
    request_id: str,
    body: ReviewDecision,
    x_reviewer_token: str | None = Header(default=None),
):
    require_reviewer(x_reviewer_token)
    r = REQUESTS.get(request_id)
    if not r:
        raise HTTPException(404, "Not found")
    if r["status"] != "pending_review":
        raise HTTPException(409, "Request is not waiting for review")

    out = graph.invoke(Command(resume={"approved": body.approved, "note": body.note}),
                       cfg(request_id))
    res = out["result"]
    r["status"], r["reply"], r["review_note"] = res["status"], res["reply"], body.note
    log.info("review %s -> %s", request_id, r["status"])  # audit trail (stdout for now)
    return {"request_id": request_id, "status": r["status"], "reply": r["reply"]}