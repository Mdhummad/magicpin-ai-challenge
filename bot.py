"""
Vera++ — magicpin AI Challenge Bot
===================================
FastAPI server exposing all 5 required endpoints:
  GET  /v1/healthz
  GET  /v1/metadata
  POST /v1/context
  POST /v1/tick
  POST /v1/reply

Run:  uvicorn bot:app --host 0.0.0.0 --port 8080
"""

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
load_dotenv(override=True)   # ← must run BEFORE composer import reads os.getenv

from composer import Composer
from conversation_handlers import ConversationHandler


app = FastAPI(title="Vera++", version="2.0.0")
START_TIME = time.time()

@app.get("/")
async def root():
    return {"service": "Vera++", "version": "2.0.0", "status": "ok"}

# ── In-memory state ────────────────────────────────────────────────────────────
# (scope, context_id)  →  {version, payload}
contexts: dict[tuple[str, str], dict] = {}

# conversation_id → {merchant_id, customer_id, trigger_id, turns[], auto_reply_count, ended}
conversations: dict[str, dict] = {}

# Suppression keys already sent (prevents duplicate sends)
used_suppression_keys: set[str] = set()

# Conversation IDs that ended (no more sends allowed)
ended_convs: set[str] = set()

# Track which (merchant_id, trigger_id) pairs we've already initiated a convo for
sent_pairs: set[tuple[str, str]] = set()

composer = Composer()
handler  = ConversationHandler()

# ── Helpers ────────────────────────────────────────────────────────────────────

def _ctx(scope: str, cid: str) -> Optional[dict]:
    entry = contexts.get((scope, cid))
    return entry["payload"] if entry else None

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _count_contexts() -> dict:
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _) in contexts:
        if scope in counts:
            counts[scope] += 1
    return counts

def _is_expired(trigger: dict) -> bool:
    exp = trigger.get("expires_at")
    if not exp:
        return False
    try:
        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > exp_dt
    except Exception:
        return False

# ── /v1/healthz ───────────────────────────────────────────────────────────────

@app.get("/v1/healthz")
async def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": _count_contexts(),
    }

# ── /v1/metadata ──────────────────────────────────────────────────────────────

@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name":    os.getenv("TEAM_NAME", "Vera++"),
        "team_members": [os.getenv("TEAM_MEMBER", "Hammad")],
        "model":        os.getenv("LLM_MODEL", "gemini-1.5-pro"),
        "approach": (
            "4-context composer with trigger-kind dispatch. "
            "Separate compulsion-lever framing per trigger kind (research_digest → curiosity+reciprocity, "
            "perf_dip → loss-aversion+social-proof, etc.). "
            "Auto-reply detection (phrase + repetition). "
            "Intent-transition routing (merchant commits → immediate action mode). "
            "Graceful hostile/off-topic handling. "
            "Hindi-English code-mix for hi-language merchants. "
            "Adaptive context: always uses latest pushed version."
        ),
        "contact_email": os.getenv("CONTACT_EMAIL", "team@example.com"),
        "version":       "2.0.0",
        "submitted_at":  "2026-04-30T13:00:00Z",
    }

# ── /v1/context ───────────────────────────────────────────────────────────────

class CtxBody(BaseModel):
    scope:        str
    context_id:   str
    version:      int
    payload:      dict[str, Any]
    delivered_at: str

VALID_SCOPES = {"category", "merchant", "customer", "trigger"}

@app.post("/v1/context")
async def push_context(body: CtxBody):
    if body.scope not in VALID_SCOPES:
        return JSONResponse(status_code=400, content={
            "accepted": False,
            "reason":   "invalid_scope",
            "details":  f"scope must be one of: {sorted(VALID_SCOPES)}",
        })

    key     = (body.scope, body.context_id)
    current = contexts.get(key)

    # Idempotent: strictly older version → reject
    if current and current["version"] > body.version:
        return JSONResponse(status_code=409, content={
            "accepted":        False,
            "reason":          "stale_version",
            "current_version": current["version"],
        })

    # Atomic replace
    contexts[key] = {"version": body.version, "payload": body.payload}

    return {
        "accepted":   True,
        "ack_id":     f"ack_{body.context_id}_v{body.version}",
        "stored_at":  _now_iso(),
    }

# ── /v1/tick ──────────────────────────────────────────────────────────────────

class TickBody(BaseModel):
    now:                str
    available_triggers: list[str] = []

@app.post("/v1/tick")
async def tick(body: TickBody):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _process_trigger(trg_id: str):
        """Compose one trigger — runs in a thread pool."""
        trg = _ctx("trigger", trg_id)
        if not trg:
            return None
        if _is_expired(trg):
            return None

        sup_key     = trg.get("suppression_key", "")
        merchant_id = trg.get("merchant_id")
        customer_id = trg.get("customer_id")

        if not merchant_id:
            return None
        if sup_key and sup_key in used_suppression_keys:
            return None
        if (merchant_id, trg_id) in sent_pairs:
            return None

        merchant = _ctx("merchant", merchant_id)
        if not merchant:
            return None

        cat_slug = merchant.get("category_slug", "")
        category = _ctx("category", cat_slug)
        if not category:
            return None

        customer = _ctx("customer", customer_id) if customer_id else None

        try:
            composed = composer.compose(category, merchant, trg, customer)
        except Exception as e:
            print(f"[COMPOSE ERROR] {trg_id}: {e}")
            return None

        if not composed or not composed.get("body"):
            return None

        return (trg_id, merchant_id, customer_id, trg, merchant, composed)

    # ── Parallel compose — fire all triggers simultaneously ───────────────────
    results = []
    max_workers = min(len(body.available_triggers), 8)  # cap at 8 threads

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process_trigger, tid): tid
                   for tid in body.available_triggers}
        for future in as_completed(futures, timeout=35):
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as e:
                print(f"[TICK THREAD ERROR] {futures[future]}: {e}")

    # ── Build actions (single-threaded — modifies shared state) ───────────────
    actions = []
    for trg_id, merchant_id, customer_id, trg, merchant, composed in results:
        sup_key = trg.get("suppression_key", "")

        # Re-check suppression (another thread may have claimed it)
        if sup_key and sup_key in used_suppression_keys:
            continue
        if (merchant_id, trg_id) in sent_pairs:
            continue

        conv_id = f"conv_{merchant_id}_{trg_id}"
        conversations[conv_id] = {
            "merchant_id":      merchant_id,
            "customer_id":      customer_id,
            "trigger_id":       trg_id,
            "trigger_payload":  trg,
            "turns": [{"from": "vera", "body": composed["body"], "ts": body.now}],
            "status":           "active",
            "auto_reply_count": 0,
            "ended":            False,
        }

        if sup_key:
            used_suppression_keys.add(sup_key)
        sent_pairs.add((merchant_id, trg_id))

        send_as = "merchant_on_behalf" if customer_id else "vera"

        actions.append({
            "conversation_id":  conv_id,
            "merchant_id":      merchant_id,
            "customer_id":      customer_id,
            "send_as":          send_as,
            "trigger_id":       trg_id,
            "template_name":    f"vera_{trg.get('kind', 'generic')}_v1",
            "template_params":  composed.get("template_params", [
                merchant.get("identity", {}).get("name", ""), composed["body"][:80], ""
            ]),
            "body":             composed["body"],
            "cta":              composed.get("cta", "open_ended"),
            "suppression_key":  sup_key,
            "rationale":        composed.get("rationale", ""),
        })

    return {"actions": actions}

# ── /v1/reply ─────────────────────────────────────────────────────────────────

class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id:     Optional[str] = None
    customer_id:     Optional[str] = None
    from_role:       str
    message:         str
    received_at:     str
    turn_number:     int

@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv_id = body.conversation_id

    # Already closed conversation
    if conv_id in ended_convs:
        return {"action": "end", "rationale": "Conversation previously closed."}

    # Bootstrap state if judge sends a reply to a conversation we didn't initiate
    if conv_id not in conversations:
        conversations[conv_id] = {
            "merchant_id":      body.merchant_id,
            "customer_id":      body.customer_id,
            "trigger_id":       "",
            "trigger_payload":  {},
            "turns":            [],
            "status":           "active",
            "auto_reply_count": 0,
            "ended":            False,
        }

    conv = conversations[conv_id]

    # Record incoming turn
    conv["turns"].append({
        "from": body.from_role,
        "body": body.message,
        "ts":   body.received_at,
    })

    # Resolve merchant + category
    merchant_id = body.merchant_id or conv.get("merchant_id")
    merchant    = _ctx("merchant", merchant_id) if merchant_id else None
    category    = _ctx("category", merchant.get("category_slug", "")) if merchant else None

    customer_id = body.customer_id or conv.get("customer_id")
    customer    = _ctx("customer", customer_id) if customer_id else None

    # Get handler response
    result = handler.handle_reply(
        conv_id=conv_id,
        conv_state=conv,
        message=body.message,
        merchant=merchant,
        category=category,
        customer=customer,
        from_role=body.from_role,
        composer=composer,
    )

    # Update state
    if result.get("action") == "end":
        conv["ended"] = True
        ended_convs.add(conv_id)
    elif result.get("action") == "send" and result.get("body"):
        conv["turns"].append({
            "from": "vera",
            "body": result["body"],
            "ts":   _now_iso(),
        })
        # Belt-and-suspenders: close if rationale signals an exit after a send
        if "closing" in result.get("rationale", "").lower() or \
           "opted out" in result.get("rationale", "").lower():
            conv["ended"] = True
            ended_convs.add(conv_id)

    return result

# ── Optional teardown ─────────────────────────────────────────────────────────

@app.post("/v1/teardown")
async def teardown():
    contexts.clear()
    conversations.clear()
    used_suppression_keys.clear()
    ended_convs.clear()
    sent_pairs.clear()
    return {"status": "wiped"}

# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=8080, reload=False)
