"""
Vera++ Conversation Handlers — Multi-turn reply logic.

Handles (in priority order):
  0. Customer reply        — direct customer voice, handled separately
  1. Hostile / opt-out     — "stop / spam / not interested" → immediate end
  2. Auto-reply detection  — WA Business canned replies → wait(1h) → wait(24h) → end
  3. "Later / busy"        — back off 30 min
  4. Intent commit         — "yes / go / confirm" → execution mode immediately
  5. Off-topic             — politely redirect in one sentence
  6. Normal engaged reply  — deterministic action-oriented response (+ optional LLM)
"""

from typing import Optional

# ── Pattern banks ─────────────────────────────────────────────────────────────

AUTO_REPLY_MARKERS = (
    "thank you for contacting",
    "will respond shortly",
    "away from whatsapp",
    "business hours",
    "auto-reply",
    "automated",
    "out of office",
    "our team will respond",
    "we will get back",
    "we'll get back",
    "kindly note that",
    "this is an automated",
    "we are currently unavailable",
    "for urgent queries",
    "aapki jaankari ke liye",
    "hamari team tak pahuncha",
)

HOSTILE_MARKERS = (
    "stop", "spam", "useless", "dont message", "don't message",
    "unsubscribe", "not interested", "remove me", "block",
    "waste of time", "bothering me", "annoying",
    "band karo", "mat bhejo", "nahi chahiye",
)

COMMITMENT_MARKERS = (
    "do it", "lets do", "let's do", "yes", "ok", "whats next",
    "what's next", "proceed", "confirm", "chalega", "go ahead",
    "haan karo", "haan chalega", "kar do", "karo", "aage badho",
    "sounds good", "done deal", "next steps",
)

OFF_TOPIC_MARKERS = (
    "gst", "income tax", "itr", "insurance claim",
    "loan", "emi", "electricity bill", "water bill",
    "legal notice", "court", "complaint consumer",
    "tax", "rent", "license renewal",
)

LATER_MARKERS = ("later", "busy", "not now", "abhi nahi", "baad mein")


def _lower(text: str) -> str:
    return text.lower().strip()


# ── Canned responses ──────────────────────────────────────────────────────────

def hostile_response() -> dict:
    return {
        "action": "end",
        "rationale": "Merchant opted out or expressed hostility. Closing conversation; suppressing for 30 days.",
    }


def auto_reply_wait_1() -> dict:
    return {
        "action": "wait",
        "wait_seconds": 3600,
        "rationale": "Detected likely business auto-reply; waiting 1 hour before retry.",
    }


def auto_reply_wait_2() -> dict:
    return {
        "action": "wait",
        "wait_seconds": 86400,
        "rationale": "Repeated auto-reply detected; waiting 24 hours.",
    }


def auto_reply_end() -> dict:
    return {
        "action": "end",
        "rationale": "Repeated canned auto-reply 3+ times; ending to avoid wasting merchant turns.",
    }


def later_response() -> dict:
    return {
        "action": "wait",
        "wait_seconds": 1800,
        "rationale": "Merchant indicated they are busy; backing off for 30 minutes.",
    }


def commitment_response(merchant: Optional[dict], conv_state: dict) -> dict:
    """Merchant committed — switch to execution mode immediately."""
    identity  = (merchant or {}).get("identity", {})
    mname     = identity.get("name", "your business")
    offers    = [o for o in (merchant or {}).get("offers", []) if o.get("status") == "active"]
    offer     = offers[0].get("title", "your active offer") if offers else "the campaign"
    trg_kind  = conv_state.get("trigger_id", "")

    if "recall" in trg_kind:
        body = (f"Done — moving to execution. Drafting recall message for your lapsed patients now. "
                f"I'll prepare 3 short variants — you pick one. Reply CONFIRM and I'll send in 60 seconds.")
    elif "perf_dip" in trg_kind:
        body = (f"On it! Running a profile audit for {mname} and preparing 3 quick fixes. "
                f"Reply CONFIRM and I'll execute all three.")
    elif "ipl" in trg_kind:
        body = (f"Done — drafting match-night broadcast for {mname} now. "
                f"One delivery-only BOGO. Reply CONFIRM and it goes live in 60 seconds.")
    else:
        body = (f"Done — moving to execution. Preparing {offer} with exact price, audience, "
                f"channel, and 7-day run dates for {mname}. Reply CONFIRM and I'll proceed.")

    return {
        "action": "send",
        "body": body,
        "cta": "binary_confirm_cancel",
        "rationale": "Merchant committed explicitly. Switched to execution mode — no re-qualifying.",
    }


def off_topic_redirect(conv_state: dict) -> dict:
    trigger = conv_state.get("trigger_payload", {})
    topic   = trigger.get("kind", "your magicpin growth").replace("_", " ")
    body    = (f"I can only help with your magicpin growth actions here. "
               f"Coming back to {topic} — I can draft the offer or post right now. Reply YES.")
    return {
        "action": "send",
        "body": body,
        "cta": "binary_yes_no",
        "rationale": "Off-topic message redirected in one sentence back to original thread.",
    }


def default_engaged_response(merchant: Optional[dict], conv_state: dict) -> dict:
    """Default: one concrete, executable next step."""
    identity = (merchant or {}).get("identity", {})
    offers   = [o for o in (merchant or {}).get("offers", []) if o.get("status") == "active"]
    offer    = offers[0].get("title", "your active offer") if offers else "an offer"
    locality = identity.get("locality") or identity.get("city", "your area")
    body     = (f"Got it. Keeping this to one executable step: {offer} for {locality}, "
                f"one audience, one measurable 7-day target. Reply YES to proceed.")
    return {
        "action": "send",
        "body": body,
        "cta": "binary_yes_no",
        "rationale": "Continue with a low-friction, action-oriented next step.",
    }


def customer_slot_response(message: str, merchant: Optional[dict], customer: Optional[dict]) -> dict:
    """Respond directly to a customer booking request."""
    identity  = (merchant or {}).get("identity", {})
    mname     = identity.get("name", "us")
    cust_id   = (customer or {}).get("identity", {})
    cust_name = cust_id.get("name", "")
    prefs     = (customer or {}).get("preferences", {})
    slots     = prefs.get("preferred_slots", [])
    slot_hint = slots[0] if slots else "your preferred time"

    # Build a customer-addressed confirmation
    greeting  = f"Hi {cust_name}" if cust_name else "Hi there"
    body      = (f"{greeting}! {mname} here. We've noted your request. "
                 f"Your slot at {slot_hint} is confirmed — we'll see you then! "
                 f"Reply CONFIRM to lock it in, or let us know a different time.")
    return {
        "action": "send",
        "body": body,
        "cta": "binary_confirm_cancel",
        "rationale": "Customer slot pick addressed directly — confirmed booking details by name.",
    }


# ── Main handler class ────────────────────────────────────────────────────────

class ConversationHandler:

    def handle_reply(
        self,
        conv_id: str,
        conv_state: dict,
        message: str,
        merchant: Optional[dict],
        category: Optional[dict],
        customer: Optional[dict],
        from_role: str,
        composer,
    ) -> dict:
        """
        Decide the next action given a merchant/customer reply.
        Returns dict with keys: action, body (if send), cta (if send),
        wait_seconds (if wait), rationale.
        """
        lowered = _lower(message)

        # ── 0. Customer reply — address them directly, no merchant logic ──────
        if from_role == "customer":
            # Try LLM for rich customer reply; fall back to deterministic slot confirm
            if merchant and category and composer:
                try:
                    turns   = conv_state.get("turns", [])
                    trigger = {"kind": conv_state.get("trigger_id", ""), "payload": {}}
                    result  = composer.compose_reply(
                        category=category,
                        merchant=merchant,
                        trigger=trigger,
                        customer=customer,
                        conversation_history=turns,
                        merchant_message=message,
                        intent="customer_reply",
                    )
                    if result and result.get("body"):
                        return {"action": "send", "body": result["body"],
                                "cta": result.get("cta", "open_ended"),
                                "rationale": result.get("rationale", "Customer reply handled.")}
                except Exception as e:
                    print(f"[CUSTOMER REPLY LLM ERROR] {e}")
            # Deterministic fallback
            return customer_slot_response(message, merchant, customer)

        # ── 1. Hostile / opt-out ──────────────────────────────────────────────
        if any(marker in lowered for marker in HOSTILE_MARKERS):
            return hostile_response()

        # ── 2. Auto-reply detection (wait→wait→end, NOT send→wait→end) ────────
        if any(marker in lowered for marker in AUTO_REPLY_MARKERS):
            count = conv_state.get("auto_reply_count", 0) + 1
            conv_state["auto_reply_count"] = count
            if count == 1:
                return auto_reply_wait_1()
            elif count == 2:
                return auto_reply_wait_2()
            else:
                return auto_reply_end()
        else:
            conv_state["auto_reply_count"] = 0  # reset on real reply

        # ── 3. "Later / busy" — back off ─────────────────────────────────────
        if any(marker in lowered for marker in LATER_MARKERS):
            return later_response()

        # ── 4. Intent commit → execution mode immediately ─────────────────────
        if any(marker in lowered for marker in COMMITMENT_MARKERS):
            return commitment_response(merchant, conv_state)

        # ── 5. Off-topic ──────────────────────────────────────────────────────
        if any(marker in lowered for marker in OFF_TOPIC_MARKERS):
            return off_topic_redirect(conv_state)

        # ── 6. Normal engaged reply ───────────────────────────────────────────
        # Try LLM first for a richer continuation; deterministic fallback if it fails
        if merchant and category and composer:
            try:
                turns   = conv_state.get("turns", [])
                trigger = {"kind": conv_state.get("trigger_id", ""), "payload": {}}
                result  = composer.compose_reply(
                    category=category,
                    merchant=merchant,
                    trigger=trigger,
                    customer=customer,
                    conversation_history=turns,
                    merchant_message=message,
                    intent="engaged",
                )
                if result and result.get("body"):
                    return {"action": "send", "body": result["body"],
                            "cta": result.get("cta", "open_ended"),
                            "rationale": result.get("rationale", "LLM continuation.")}
            except Exception as e:
                print(f"[REPLY LLM ERROR] {e}")

        return default_engaged_response(merchant, conv_state)
