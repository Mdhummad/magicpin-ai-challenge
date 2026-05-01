"""
Vera++ Conversation Handlers — Multi-turn reply logic.

Handles:
  1. Auto-reply detection  — WA Business canned auto-replies (exit after 2-3 repeats)
  2. Intent transition     — "yes / let's do it / ok go" → switch to ACTION mode immediately
  3. Hostile / opt-out     — "stop / spam / not interested" → graceful end
  4. Off-topic / curveball — "help me with GST?" → politely decline + redirect
  5. Normal engaged reply  — advance conversation via LLM composer
"""

import re
from typing import Optional

# ── Pattern banks ─────────────────────────────────────────────────────────────

AUTO_REPLY_PHRASES = [
    "thank you for contacting",
    "our team will respond",
    "automated message",
    "automated assistant",
    "we will get back",
    "we'll get back",
    "kindly note that",
    "this is an automated",
    "our business hours",
    "we are currently unavailable",
    "for urgent queries",
    "aapki jaankari ke liye",       # common Hindi auto-reply
    "hamari team tak pahuncha",
    "main aapki yeh sabhi baatein",
]

INTENT_COMMIT_PHRASES = [
    "let's do it", "lets do it", "ok let's do it",
    "ok do it", "go ahead", "please proceed",
    "sounds good", "yes go", "yes please do",
    "yes, proceed", "haan karo", "haan chalega",
    "chalega", "kar do", "karo",
    "ok great", "confirm", "yes confirm",
    "ok go", "done deal", "what's next", "whats next",
    "next steps", "aage badho",
]

HOSTILE_PHRASES = [
    "stop messaging", "stop sending", "don't message",
    "dont message", "not interested", "remove me",
    "unsubscribe", "block", "spam", "waste of time",
    "useless", "bothering me", "annoying",
    "band karo", "mat bhejo", "nahi chahiye",
]

OFF_TOPIC_SIGNALS = [
    "gst", "income tax", "itr", "insurance claim",
    "loan", "emi", "electricity bill", "water bill",
    "legal notice", "court", "complaint consumer",
]


def _lower(text: str) -> str:
    return text.lower().strip()


def detect_auto_reply(message: str, prev_messages: list[str]) -> bool:
    """True if this message looks like a WA Business auto-reply."""
    msg_lower = _lower(message)

    # Phrase match — always definitive
    phrase_match = any(phrase in msg_lower for phrase in AUTO_REPLY_PHRASES)
    if phrase_match:
        # Escalate if we've seen this exact phrase before
        if prev_messages.count(message.strip()) >= 1:
            return True  # second+ occurrence
        return True      # first occurrence with phrase match

    # Repetition-only (no phrase) — only flag after 3+ identical messages
    # This prevents false positives on legitimate repeated questions.
    if prev_messages.count(message.strip()) >= 3:
        return True

    return False


def detect_intent_commit(message: str) -> bool:
    """True if merchant is explicitly committing to proceed."""
    msg_lower = _lower(message)
    for phrase in INTENT_COMMIT_PHRASES:
        if phrase in msg_lower:
            return True
    return False


def detect_hostile(message: str) -> bool:
    """True if merchant is expressing hostility or opting out."""
    msg_lower = _lower(message)
    for phrase in HOSTILE_PHRASES:
        if phrase in msg_lower:
            return True
    return False


def detect_off_topic(message: str) -> bool:
    """True if merchant asks something clearly outside Vera's scope."""
    msg_lower = _lower(message)
    for signal in OFF_TOPIC_SIGNALS:
        if signal in msg_lower:
            return True
    return False


# ── Canned graceful responses ─────────────────────────────────────────────────

def auto_reply_response_1(merchant_name: str, use_hindi: bool) -> dict:
    """First auto-reply detected — try to reach the owner."""
    if use_hindi:
        body = (f"Lagta hai yeh auto-reply hai. "
                f"Jab owner/manager dekhein, please reply karein — ek simple 'YES' kaafi hai.")
    else:
        body = ("Looks like an auto-reply. "
                "When the owner sees this, a simple 'YES' is all it takes to continue.")
    return {"action": "send", "body": body, "cta": "binary_yes_no",
            "rationale": "Detected auto-reply (phrase match). One explicit prompt to reach the owner."}


def auto_reply_response_2() -> dict:
    """Second consecutive auto-reply — back off and wait."""
    return {"action": "wait", "wait_seconds": 86400,
            "rationale": "Same auto-reply twice in a row. Owner not at phone. Waiting 24h before retry."}


def auto_reply_response_3() -> dict:
    """Third consecutive auto-reply — end the conversation."""
    return {"action": "end",
            "rationale": "Auto-reply 3× in a row with no real reply. Closing conversation."}


def hostile_response(use_hindi: bool) -> dict:
    """End the conversation immediately on hostile/opt-out message."""
    return {
        "action": "end",
        "rationale": "Merchant opted out or expressed hostility. Closing conversation; suppressing for 30 days.",
    }


def off_topic_redirect(original_topic: str, use_hindi: bool) -> dict:
    if use_hindi:
        body = (f"Woh cheez mere scope ke bahar hai, uske liye specialist se milein. "
                f"Wapas original topic par — {original_topic}. Kya aage barhein?")
    else:
        body = (f"That's outside what I can help with — you'll need a specialist for that. "
                f"Coming back to {original_topic} — shall we continue?")
    return {"action": "send", "body": body, "cta": "binary_yes_no",
            "rationale": "Off-topic ask politely declined in 1 sentence; redirected to original thread."}


# ── Main handler class ────────────────────────────────────────────────────────

class ConversationHandler:

    def handle_reply(
        self,
        conv_id: str,
        conv_state: dict,
        message: str,
        merchant: Optional[dict],
        category: Optional[dict],
        composer,
    ) -> dict:
        """
        Decide the next action given a merchant/customer reply.
        Returns dict with keys: action, body (if send), cta (if send),
        wait_seconds (if wait), rationale.
        """
        identity   = (merchant or {}).get("identity", {})
        langs      = identity.get("languages", ["en"])
        use_hindi  = "hi" in langs or "hi-en" in str(langs)
        owner_name = identity.get("owner_first_name", identity.get("name", ""))

        turns       = conv_state.get("turns", [])
        prev_vera   = [t["body"] for t in turns if t.get("from") == "vera"]
        prev_merchant = [t["body"] for t in turns if t.get("from") == "merchant"]
        auto_count  = conv_state.get("auto_reply_count", 0)

        # ── 1. Hostile / opt-out ──────────────────────────────────────────────
        if detect_hostile(message):
            return hostile_response(use_hindi)

        # ── 2. Auto-reply detection ───────────────────────────────────────────
        if detect_auto_reply(message, prev_merchant):
            new_count = auto_count + 1
            conv_state["auto_reply_count"] = new_count
            if new_count == 1:
                return auto_reply_response_1(owner_name, use_hindi)
            elif new_count == 2:
                return auto_reply_response_2()
            else:
                return auto_reply_response_3()
        else:
            conv_state["auto_reply_count"] = 0  # reset on real reply

        # ── 3. Intent commit → immediate action mode ──────────────────────────
        if detect_intent_commit(message):
            return self._action_mode_reply(
                conv_state, merchant, category, message, use_hindi, composer
            )

        # ── 4. Off-topic ──────────────────────────────────────────────────────
        if detect_off_topic(message):
            trigger = conv_state.get("trigger_payload", {})
            topic   = trigger.get("kind", "your profile").replace("_", " ")
            return off_topic_redirect(topic, use_hindi)

        # ── 5. Normal engaged reply — use LLM to compose continuation ─────────
        if not merchant or not category or not composer:
            return {"action": "send",
                    "body": "Got it! Let me take care of that right away. Anything else you'd like?",
                    "cta": "open_ended",
                    "rationale": "Normal reply — generic follow-up (no context available)."}

        return self._llm_reply(
            conv_state, merchant, category, message, use_hindi, composer
        )

    # ── Intent commit mode ────────────────────────────────────────────────────

    def _action_mode_reply(self, conv_state, merchant, category, merchant_msg, use_hindi, composer):
        """Merchant said yes/let's do it — switch to action execution immediately."""
        try:
            offers    = [o for o in (merchant or {}).get("offers", []) if o.get("status") == "active"]
            trg_kind  = conv_state.get("trigger_id", "")

            if "recall" in trg_kind or "recall" in str(conv_state.get("turns", "")):
                action_body = self._recall_action(merchant, use_hindi)
            elif "research" in trg_kind or "digest" in trg_kind:
                action_body = self._digest_action(merchant, category, use_hindi)
            elif "perf_dip" in trg_kind:
                action_body = self._perf_action(merchant, use_hindi)
            elif "planning" in trg_kind:
                action_body = self._planning_action(merchant, use_hindi)
            else:
                action_body = self._generic_action(merchant, use_hindi)
        except Exception as e:
            print(f"[ACTION MODE ERROR] {e}")
            action_body = "On it! Drafting your next step now — will share in 60 seconds. Reply CONFIRM."

        return {"action": "send", "body": action_body, "cta": "binary_confirm_cancel",
                "rationale": "Merchant committed explicitly. Switched to action mode immediately — no re-qualifying."}

    def _recall_action(self, merchant, use_hindi):
        name = merchant.get("identity", {}).get("name", "")
        agg  = merchant.get("customer_aggregate", {})
        count= agg.get("lapsed_180d_plus", agg.get("lapsed_90d_plus", 0))
        if use_hindi:
            return (f"Perfect! Drafting recall messages for {count} lapsed patients abhi. "
                    f"Main 3 message variants banaata hoon — aap choose karein. "
                    f"Reply CONFIRM to send draft.")
        return (f"On it! Drafting recall messages for your {count} lapsed patients now. "
                f"I'll prepare 3 variants — you pick one. Reply CONFIRM to see the draft.")

    def _digest_action(self, merchant, category, use_hindi):
        digest = (category or {}).get("digest", [{}])
        item   = digest[0] if digest else {}
        title  = item.get("title", "the latest research")
        if use_hindi:
            return (f"Bhej raha hoon abstract + patient-ed WhatsApp draft abhi. "
                    f"Topic: {title}. "
                    f"GBP post ke liye bhi draft ready karun? Reply CONFIRM.")
        return (f"Sending abstract + drafting patient-ed WhatsApp now. Topic: {title}. "
                f"Want me to also prep a GBP post? Reply CONFIRM.")

    def _perf_action(self, merchant, use_hindi):
        perf = (merchant or {}).get("performance", {})
        delta = (perf.get("delta_7d") or {}).get("calls_pct", 0) or 0
        pct   = int(delta * 100)
        if use_hindi:
            return (f"Dekh raha hoon — calls {abs(pct)}% neeche hain. "
                    f"Main profile audit + 3 quick fixes draft karta hoon. Reply CONFIRM.")
        return (f"Looking at your numbers — calls are down {abs(pct)}%. "
                f"Drafting a profile audit + 3 quick fixes. Reply CONFIRM to proceed.")

    def _planning_action(self, merchant, use_hindi):
        if use_hindi:
            return ("Draft ready kar raha hoon — 60 seconds. "
                    "Pricing, format, aur GBP post sabh include hoga. Reply CONFIRM.")
        return ("Drafting now — 60 seconds. I'll include pricing, format, and a GBP post. Reply CONFIRM.")

    def _generic_action(self, merchant, use_hindi):
        name = (merchant or {}).get("identity", {}).get("name", "your business")
        if use_hindi:
            return f"Shukriya! {name} ke liye action le raha hoon abhi. Ek minute mein update dunga. Reply CONFIRM."
        return f"Great! Taking action for {name} right now. Will update you in a minute. Reply CONFIRM."

    # ── LLM continuation reply ────────────────────────────────────────────────

    def _llm_reply(self, conv_state, merchant, category, merchant_msg, use_hindi, composer):
        turns    = conv_state.get("turns", [])
        trigger  = {"kind": conv_state.get("trigger_id", ""), "payload": {}}
        try:
            result = composer.compose_reply(
                category=category,
                merchant=merchant,
                trigger=trigger,
                customer=None,
                conversation_history=turns,
                merchant_message=merchant_msg,
                intent="engaged",
            )
            if result and result.get("body"):
                return {"action": "send", "body": result["body"],
                        "cta": result.get("cta", "open_ended"),
                        "rationale": result.get("rationale", "LLM continuation.")}
        except Exception as e:
            print(f"[REPLY LLM ERROR] {e}")

        # Fallback
        return {"action": "send",
                "body": "Got it! Working on that for you now — reply YES to confirm.",
                "cta": "binary_yes_no",
                "rationale": "LLM reply failed — safe fallback used."}
