"""
Vera++ Composer — 4-context LLM message composition engine.

Strategy:
  - Trigger-kind dispatch: different compulsion lever framing per kind
  - Anti-hallucination: only uses facts from provided contexts
  - Hindi-English code-mix when merchant language includes 'hi'
  - No URLs, single CTA, peer-voice, specificity-first
"""

import os
import json
import re
import traceback
import urllib.request
from typing import Optional
from dotenv import load_dotenv

load_dotenv(override=True)

# ── Compulsion lever hints per trigger kind ──────────────────────────────────
LEVER_MAP = {
    "research_digest":         "curiosity + reciprocity. OPEN with the finding + exact source. End: 'Want me to draft a patient note around this?'",
    "regulation_change":       "loss aversion (deadline) + effort externalization. Say EXACTLY what changes and that you've flagged what to update.",
    "perf_dip":                "loss aversion + social proof. Lead with the EXACT % drop + peer gap. End binary: 'Want me to fix this now? YES/NO'",
    "perf_spike":              "reciprocity (celebrate it) + curiosity. End: 'Want to see what drove it?'",
    "milestone_reached":       "social proof + reciprocity. Name the milestone number. End: 'Want me to share this with your regulars?'",
    "recall_due":              "effort externalization — say 'I've already lined up the recall message'. Binary CTA: 'Send it? YES/NO'",
    "festival_upcoming":       "loss aversion + specificity. Name the festival date. Say booking window closes in X days.",
    "competitor_opened":       "loss aversion + curiosity. Name the competitor locality. End: 'Want to see their listing?'",
    "renewal_due":             "loss aversion (profile pauses on exact expiry date). Name the date. End: 'Renew now? YES/NO'",
    "winback_eligible":        "effort externalization + loss aversion. Say 'I've drafted a winback message for N lapsed customers'. Binary CTA.",
    "dormant_with_vera":       "curiosity + light ask. No pressure. Just one question about their business.",
    "curious_ask_due":         "pure curiosity — ask ONE question. NO CTA at all.",
    "review_theme_emerged":    "reciprocity (you noticed) + effort externalization. Name the theme + exact count.",
    "ipl_match_today":         "specificity (tonight's match name) + time-bound loss aversion. 'Last orders before 7:30pm'",
    "active_planning_intent":  "effort externalization — 'I've already drafted it'. Binary: 'Confirm and I'll send in 60 seconds'",
    "customer_lapsed_hard":    "effort externalization + loss aversion. Name the patient/customer + last visit date. Binary CTA.",
    "trial_followup":          "reciprocity + binary slot CTA. Name the trial service + date.",
    "chronic_refill_due":      "urgency (stock runs out date) + effort externalization.",
    "supply_alert":            "urgency (recall batch IDs) + effort externalization.",
    "seasonal_perf_dip":       "social proof (expected seasonal pattern) + specific next-step nudge.",
    "gbp_unverified":          "loss aversion — name EXACT % views uplift possible. Say 'I can guide you through in 10 mins'.",
    "category_seasonal":       "specificity (demand % numbers) + shelf-action CTA.",
    "cde_opportunity":         "reciprocity + low-friction (free, N credits). Name the exact event.",
    "wedding_package_followup":"loss aversion (wedding date approaching) + effort externalization. Name the bride/groom.",
    "generic":                 "specificity + effort externalization + single binary CTA. Start with a fact, end with YES/NO.",
}


class Composer:
    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "gemini").lower()
        self.api_key  = os.getenv("LLM_API_KEY", "")
        self.model    = os.getenv("LLM_MODEL", "")

    # ── Public API ────────────────────────────────────────────────────────────

    def compose(
        self,
        category: dict,
        merchant: dict,
        trigger: dict,
        customer: Optional[dict] = None,
        conversation_history: Optional[list] = None,
    ) -> dict:
        """Return dict with: body, cta, template_params, rationale."""
        system = self._build_system(category, merchant, trigger, customer)
        user   = self._build_user(category, merchant, trigger, customer, conversation_history)
        raw    = self._call_llm(system, user)
        result = self._parse(raw, merchant, trigger, customer)

        # If we got a fallback (LLM returned bad JSON), retry once with a stricter prompt
        if result.get("rationale") == "LLM parse failed — fallback message used.":
            retry_user = user + "\n\nCRITICAL: Your previous response was not valid JSON. Output ONLY the raw JSON object. No markdown, no explanation, no ```json fences. Start your response with { and end with }."
            raw2   = self._call_llm(system, retry_user)
            result = self._parse(raw2, merchant, trigger, customer)

        return result

    def compose_reply(
        self,
        category: dict,
        merchant: dict,
        trigger: Optional[dict],
        customer: Optional[dict],
        conversation_history: list,
        merchant_message: str,
        intent: str,           # "engaged" | "off_topic" | "curveball"
    ) -> dict:
        """Compose a follow-up reply in a live conversation."""
        system = self._build_reply_system(category, merchant, intent)
        user   = self._build_reply_user(
            category, merchant, trigger, customer,
            conversation_history, merchant_message, intent
        )
        raw  = self._call_llm(system, user)
        data = self._parse(raw, merchant, trigger or {}, customer)
        return data

    # ── System prompts ────────────────────────────────────────────────────────

    def _build_system(self, category, merchant, trigger, customer):
        voice      = category.get("voice", {})
        tone       = voice.get("tone", "peer_professional")
        taboos     = voice.get("vocab_taboo", [])
        kind       = trigger.get("kind", "generic")
        levers     = LEVER_MAP.get(kind, LEVER_MAP["generic"])
        lang_hint  = self._lang_hint(merchant)
        send_as    = "merchant_on_behalf" if customer else "vera"

        return f"""You are Vera, magicpin's AI merchant assistant composing a WhatsApp message.

SCORING DIMENSIONS you must maximise (0-10 each, judge is strict):
1. SPECIFICITY  — anchor on a VERIFIABLE fact: exact ₹ price, %, count, date, source citation.
   BAD: "Your performance has dipped."  GOOD: "Your calls dropped 18% this week vs peer median of 3.5% CTR."
2. CATEGORY FIT — tone="{tone}". FORBIDDEN words: {taboos}. Match the vertical's communication style exactly.
3. MERCHANT FIT — use THIS merchant's actual numbers. Name them. Reference their locality, plan, real stats.
4. TRIGGER RELEVANCE — The trigger is the FIRST SENTENCE, not a footnote. WHY NOW must be unmistakable. You MUST explicitly connect the message to the TRIGGER EVENT in the very first sentence. Use specific data from the trigger payload to justify this message!
5. ENGAGEMENT COMPULSION — {levers}. You MUST create a powerful sense of loss aversion, urgency, or curiosity. Ensure the merchant feels they are losing money/customers or missing out right now if they don't reply. Make the ask irresistible.

ENGAGEMENT RULES (critical for score — judge penalises vague CTAs heavily):
- EFFORT EXTERNALIZATION: Vera has already done the work. Say it explicitly:
  "I've already drafted this for you." / "I've lined up the recall slots." / "Message is ready to send."
- BINARY CTA: Never say "let me know" or "feel free to reach out".
  Say: "Reply YES and I'll send it in 60 seconds" or "Shall I go ahead? YES/NO"
- OPEN WITH IMPACT AND TRIGGER: First sentence = the most compelling fact from the trigger explaining WHY NOW. No warm-up phrases.
- SOCIAL PROOF WITH NUMBERS: Don't say "peers" — say "top {category.get('slug','')} clinics average X% CTR".

HARD RULES:
- Single CTA, placed at the END of the message.
- NO URLs (Meta rejects them). Zero tolerance.
- NO fabricated data. Use only what is in the context below.
- NO preambles ("Hope you're well", "Just checking in", "I'm reaching out")
- NO re-introductions after the first message in a conversation.
- Body: 2-4 concise WhatsApp sentences. No bullet lists.
- send_as: "{send_as}"
- {lang_hint}

OUTPUT: ONLY a JSON object, no markdown fences, no commentary:
{{"body":"<message>","cta":"open_ended"|"binary_yes_no"|"binary_confirm_cancel"|"none","template_params":["<p1>","<p2>","<p3>"],"rationale":"<2-3 sentences: which facts anchored this + which lever used>"}}"""

    def _build_reply_system(self, category, merchant, intent):
        voice  = category.get("voice", {})
        tone   = voice.get("tone", "peer_professional")
        taboos = voice.get("vocab_taboo", [])
        lang_hint = self._lang_hint(merchant)

        intent_rule = {
            "engaged":   "Merchant accepted / is engaged. Move IMMEDIATELY to the next action step. Do NOT re-qualify.",
            "off_topic": "Merchant asked something outside your scope. Politely decline in 1 sentence, then redirect to the original topic.",
            "curveball": "Unexpected reply. Stay on-mission, address briefly, then redirect.",
            "customer_reply": "Customer sent a message. Address the customer directly and handle their request (e.g. booking a slot) in a helpful, customer-facing tone.",
        }.get(intent, "Respond naturally and advance the conversation.")

        intro = "You are Vera replying in an ongoing WhatsApp conversation with a merchant."
        if intent == "customer_reply":
            intro = "You are replying directly to a CUSTOMER on behalf of the merchant."

        return f"""{intro}
Tone: {tone}. Forbidden words: {taboos}.
Detected intent: {intent_rule}
{lang_hint}
OUTPUT: ONLY a JSON object:
{{"body":"<reply>","cta":"open_ended"|"binary_yes_no"|"binary_confirm_cancel"|"none","rationale":"<1-2 sentences>"}}"""

    # ── User prompts ──────────────────────────────────────────────────────────

    def _build_user(self, category, merchant, trigger, customer, conv_hist):
        identity   = merchant.get("identity", {})
        perf       = merchant.get("performance", {})
        peer       = category.get("peer_stats", {})
        active_offers = [o for o in merchant.get("offers", []) if o.get("status") == "active"]
        signals    = merchant.get("signals", [])
        digest     = category.get("digest", [])
        trg_kind   = trigger.get("kind", "generic")
        trg_payload= dict(trigger.get("payload", {}))

        # ── Trigger-Kind Dispatch Table ───────────────────────────────────────
        dispatch_instruction = ""
        if trg_kind == "regulation_change":
            dispatch_instruction = "ROUTING INSTRUCTION: regulation_change -> Compose a DCI compliance alert with the exact deadline, dose change, and a clear action item. DO NOT mention CTR, performance, or patient recall."
        elif trg_kind == "perf_dip":
            if "delta_pct" in trg_payload:
                try:
                    pct = int(abs(float(trg_payload["delta_pct"])) * 100)
                    trg_payload["parsed_delta"] = f"{pct}% drop"
                except:
                    pass
            dispatch_instruction = "ROUTING INSTRUCTION: perf_dip -> Compose a metric explainer focusing on the recent drop in performance. State the exact percentage drop (use 'parsed_delta' if available, NOT the raw decimal ratio) and compare against the peer median. Ask if they want you to fix it."
        elif trg_kind == "ipl_match_today":
            dispatch_instruction = "ROUTING INSTRUCTION: ipl_match_today -> Compose a time-bound alert for tonight's IPL match. Name the teams playing and suggest a last-minute broadcast to drive orders before the match starts. End with a binary YES/NO."
        elif trg_kind == "recall_due":
            dispatch_instruction = "ROUTING INSTRUCTION: recall_due -> Frame as effort externalization. Say you've drafted the recall message and ask for confirmation to send."
        elif trg_kind == "competitor_opened":
            dispatch_instruction = "ROUTING INSTRUCTION: competitor_opened -> Name the competitor and locality. Use curiosity and loss aversion. Ask if they want to see the competitor's listing."
        elif trg_kind == "review_theme_emerged":
            dispatch_instruction = "ROUTING INSTRUCTION: review_theme_emerged -> State the emerging review theme and exact count. Ask if they want a drafted response."
        else:
            dispatch_instruction = "ROUTING INSTRUCTION: Follow the standard lever hints."

        # resolve referenced digest item
        top_item_id = trg_payload.get("top_item_id") or trg_payload.get("digest_item_id")
        digest_item = next((d for d in digest if d.get("id") == top_item_id), None)

        ctr      = perf.get("ctr", 0)
        peer_ctr = peer.get("avg_ctr", 0)
        ctr_label= f"{ctr:.1%} (peer median {peer_ctr:.1%}, {'BELOW' if ctr < peer_ctr else 'ABOVE'})"

        customer_block = self._customer_block(customer) if customer else ""
        hist_block     = self._history_block(conv_hist or merchant.get("conversation_history", []))

        # Pre-compute JSON strings outside f-string to avoid Python misinterpreting
        # dict literals inside f-expressions as set literals (unhashable bug)
        digest_summary = [{"id": d.get("id"), "title": d.get("title"),
                           "source": d.get("source")} for d in digest]
        digest_json    = json.dumps(digest_summary, ensure_ascii=False)
        digest_item_line = (
            f"digest_item_referenced: {json.dumps(digest_item, ensure_ascii=False)}"
            if digest_item else ""
        )
        trg_payload_json = json.dumps(trg_payload, ensure_ascii=False)
        cust_agg_json    = json.dumps(merchant.get("customer_aggregate", {}), ensure_ascii=False)
        review_json      = json.dumps(merchant.get("review_themes", [])[:2], ensure_ascii=False)

        return f"""=== TRIGGER (WHY NOW) ===
kind: {trg_kind}  |  source: {trigger.get('source')}  |  urgency: {trigger.get('urgency')}/5
payload: {trg_payload_json}
{digest_item_line}
suppression_key: {trigger.get('suppression_key','')}
{dispatch_instruction}

=== CATEGORY ===
slug: {category.get('slug')}  |  tone: {category.get('voice',{}).get('tone')}
peer: avg_rating={peer.get('avg_rating')}, avg_reviews={peer.get('avg_reviews')}, avg_ctr={peer.get('avg_ctr')}
offer_catalog (top 3): {[o.get('title') for o in category.get('offer_catalog',[])[:3]]}
seasonal_beats: {[s.get('note') for s in category.get('seasonal_beats',[])[:2]]}
trend_signals: {[str(t.get('query','')) + ' +' + str(int((t.get('delta_yoy') or 0)*100)) + '% YoY' for t in category.get('trend_signals',[])[:2]]}
digest (all items): {digest_json}

=== MERCHANT ===
name: {identity.get('name')}  |  owner: {identity.get('owner_first_name')}
city: {identity.get('city')}  |  locality: {identity.get('locality')}
languages: {identity.get('languages',['en'])}  |  verified: {identity.get('verified')}
subscription: {merchant.get('subscription',{}).get('status')} / {merchant.get('subscription',{}).get('plan')} / {merchant.get('subscription',{}).get('days_remaining','?')}d left
performance (30d): views={perf.get('views')}, calls={perf.get('calls')}, directions={perf.get('directions')}, CTR={ctr_label}
7d delta: {perf.get('delta_7d',{})}
active_offers: {[o.get('title') for o in active_offers] or 'none'}
signals: {signals}
customer_aggregate: {cust_agg_json}
review_themes: {review_json}
{hist_block}
{customer_block}
Compose the message now. Output ONLY the JSON object."""

    def _build_reply_user(self, category, merchant, trigger, customer, conv_hist, merchant_msg, intent):
        identity = merchant.get("identity", {})
        return f"""=== CONVERSATION SO FAR ===
{self._history_block(conv_hist)}

=== MERCHANT JUST SAID ===
"{merchant_msg}"

=== MERCHANT CONTEXT (quick ref) ===
name: {identity.get('name')}  |  signals: {merchant.get('signals',[])}
active_offers: {[o.get('title') for o in merchant.get('offers',[]) if o.get('status')=='active'] or 'none'}
trigger_kind: {trigger.get('kind','') if trigger else 'unknown'}

Reply now. Output ONLY the JSON object."""

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _lang_hint(self, merchant):
        langs = merchant.get("identity", {}).get("languages", ["en"])
        if "hi" in langs or "hi-en" in str(langs):
            return "Use Hindi-English code-mix naturally — like a WhatsApp message from a colleague."
        if "te" in langs or "kn" in langs or "ta" in langs or "mr" in langs:
            return "Use English with occasional regional warmth (no formal stiffness)."
        return "Use clear Indian English — operator tone, not formal."

    def _customer_block(self, customer):
        if not customer:
            return ""
        rel   = customer.get("relationship", {})
        cid   = customer.get("identity", {})
        prefs = customer.get("preferences", {})
        svc   = rel.get("services_received", [])
        last_svc  = svc[-1] if svc else "previous service"
        last_visit = rel.get("last_visit", "recently")
        cust_name  = cid.get("name", "the customer")
        preferred  = prefs.get("preferred_slots", "any")
        return f"""
=== CUSTOMER (send_as=merchant_on_behalf) ===
PERSONALIZATION REQUIRED: Address {cust_name} by first name. Reference their last service ({last_svc}) and last visit ({last_visit}) explicitly in the message.
name: {cust_name}  |  language: {cid.get('language_pref')}  |  age_band: {cid.get('age_band')}
state: {customer.get('state')}  |  visits: {rel.get('visits_total',0)}  |  LTV: Rs.{rel.get('lifetime_value',0)}
last_visit: {last_visit}  |  last_service: {last_svc}
all_services: {', '.join(svc) if svc else 'none'}
preferred_slots: {preferred}  ← mention this slot in the CTA
consent_scope: {customer.get('consent',{}).get('scope',[])}"""

    def _history_block(self, hist):
        if not hist:
            return "conversation_history: (none)"
        lines = ["conversation_history (last 3 turns):"]
        for h in hist[-3:]:
            lines.append(f"  [{h.get('from','?')}]: {str(h.get('body',''))[:120]}")
        return "\n".join(lines)

    # ── LLM dispatch ──────────────────────────────────────────────────────────

    def _call_llm(self, system: str, user: str) -> str:
        dispatch = {
            "gemini":    self._gemini,
            "anthropic": self._anthropic,
            "openai":    self._openai,
            "deepseek":  self._deepseek,
            "groq":      self._groq,
        }
        fn = dispatch.get(self.provider)
        if not fn:
            raise ValueError(f"Unknown LLM provider: {self.provider}")
        return fn(system, user)

    def _http_post(self, url, headers, body_dict, timeout=25):
        import time as _time
        body = json.dumps(body_dict, ensure_ascii=False).encode("utf-8")
        last_err = None
        for attempt in range(5):
            try:
                req  = urllib.request.Request(url, data=body, headers=headers)
                resp = urllib.request.urlopen(req, timeout=timeout)
                return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (429, 503):  # rate limit / overloaded
                    wait = [4, 8, 15, 30, 60][attempt]  # progressive back-off
                    print(f"[RETRY] {e.code} on attempt {attempt+1}, waiting {wait}s...")
                    _time.sleep(wait)
                    last_err = e
                    continue
                raise
        raise last_err

    def _gemini(self, system, user):
        model = self.model or "gemini-2.0-flash-lite"
        url   = (f"https://generativelanguage.googleapis.com/v1beta"
                 f"/models/{model}:generateContent?key={self.api_key}")
        data  = self._http_post(url, {"Content-Type": "application/json"}, {
            "contents": [{"parts": [{"text": f"{system}\n\n{user}"}]}],
            "generationConfig": {"temperature": 0.05, "maxOutputTokens": 600},
        })
        return data["candidates"][0]["content"]["parts"][0]["text"]

    def _anthropic(self, system, user):
        model = self.model or "claude-3-5-sonnet-20241022"
        data  = self._http_post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.api_key, "Content-Type": "application/json",
             "anthropic-version": "2023-06-01"},
            {"model": model, "max_tokens": 600, "temperature": 0.05,
             "system": system, "messages": [{"role": "user", "content": user}]},
        )
        return data["content"][0]["text"]

    def _openai(self, system, user):
        model = self.model or "gpt-4o"
        data  = self._http_post(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            {"model": model, "temperature": 0.05, "max_tokens": 600,
             "messages": [{"role": "system", "content": system},
                          {"role": "user",   "content": user}]},
        )
        return data["choices"][0]["message"]["content"]

    def _deepseek(self, system, user):
        model = self.model or "deepseek-chat"
        data  = self._http_post(
            "https://api.deepseek.com/v1/chat/completions",
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            {"model": model, "temperature": 0.05, "max_tokens": 600,
             "messages": [{"role": "system", "content": system},
                          {"role": "user",   "content": user}]},
        )
        return data["choices"][0]["message"]["content"]

    def _groq(self, system, user):
        model = self.model or "llama-3.3-70b-versatile"
        data  = self._http_post(
            "https://api.groq.com/openai/v1/chat/completions",
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
             "User-Agent": "vera-bot/2.0"},
            {"model": model, "temperature": 0.05, "max_tokens": 600,
             "messages": [{"role": "system", "content": system},
                          {"role": "user",   "content": user}]},
        )
        return data["choices"][0]["message"]["content"]

    # ── Response parsing ──────────────────────────────────────────────────────

    def _parse(self, raw: str, merchant: dict, trigger: dict, customer) -> dict:
        m = re.search(r'\{[\s\S]*?\}(?=\s*$|\s*\n)', raw)
        if not m:
            m = re.search(r'\{[\s\S]*\}', raw)
        if not m:
            return self._fallback(merchant, trigger)
        try:
            d = json.loads(m.group())
        except json.JSONDecodeError:
            return self._fallback(merchant, trigger)

        body = d.get("body", "").strip()
        if not body:
            return self._fallback(merchant, trigger)

        # Strip any accidental URLs
        body = re.sub(r'https?://\S+', '', body).strip()

        name = merchant.get("identity", {}).get("name", "")
        return {
            "body":            body,
            "cta":             d.get("cta", "open_ended"),
            "template_params": d.get("template_params", [name, body[:80], ""]),
            "rationale":       d.get("rationale", "Composed via 4-context framework."),
        }

    def _fallback(self, merchant: dict, trigger: dict) -> dict:
        identity = merchant.get("identity", {})
        name     = identity.get("name", "")
        kind     = trigger.get("kind", "update")
        return {
            "body":            f"Hi {name}, quick update on your {kind.replace('_',' ')}. Reply YES to learn more.",
            "cta":             "binary_yes_no",
            "template_params": [name, kind, ""],
            "rationale":       "LLM parse failed — fallback message used.",
        }
