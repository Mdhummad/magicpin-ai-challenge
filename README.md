# Vera++ — magicpin AI Challenge Submission

## Scores (local judge run)
| Dimension | Score |
|---|---|
| Specificity | **8/10** |
| Category Fit | **8/10** |
| Merchant Fit | **7/10** |
| Decision Quality | **6/10** |
| Engagement | **7/10** |
| **Average** | **36/50 (72%)** |

---

## Approach

**4-context composer with trigger-kind dispatch and compulsion-lever selection.**

### Architecture

```
Judge → POST /v1/context  →  In-memory context store (scope, id, version)
Judge → POST /v1/tick     →  Trigger loop → Composer → LLM → action[]
Judge → POST /v1/reply    →  ConversationHandler → intent classify → reply
```

### How the Composer Works

Every message goes through a **trigger-kind-specific system prompt** that selects the right compulsion lever:

| Trigger Kind | Primary Lever(s) |
|---|---|
| `research_digest` | Curiosity + Reciprocity — open with the finding + exact source |
| `perf_dip` | Loss aversion + Social proof — exact % drop vs peer median |
| `renewal_due` | Loss aversion — exact expiry date + binary YES/NO CTA |
| `recall_due` | Effort externalization — "I've already lined up the slots" |
| `competitor_opened` | Loss aversion + Curiosity — name the competitor locality |
| `curious_ask_due` | Pure curiosity — one question, no CTA |

The composer injects **exact numbers** from context (CTR vs peer median, lapsed patient count, digest source/trial N, subscription days remaining) to maximise specificity scores.

### Multi-turn Conversation Handling

`conversation_handlers.py` detects four states before calling the LLM:

1. **Auto-reply** — phrase match + repetition detection → try once, then `wait 24h`, then `end`
2. **Intent commit** — "let's do it / chalega / ok go" → switches to **action mode** (no re-qualifying)
3. **Hostile / opt-out** — "stop / spam / not interested" → one-line apology + `end`
4. **Off-topic** — "help with GST?" → politely decline in 1 sentence + redirect

### Language Handling

- Hindi in `identity.languages` → Hindi-English code-mix throughout
- South Indian language → English with regional warmth
- Default → Indian-English operator tone

### Model Choice

**Groq (llama-3.3-70b-versatile)** — fastest free-tier inference, ~1s latency per composition. Falls back to a deterministic template if the LLM is unreachable.

### What I Would Do With More Time

- Real-time Google Trends API per locality (currently uses static `trend_signals`)
- Merchant's actual open slot calendar for `recall_due`
- A/B prompt versioning — track which lever variant drives higher reply rates
- Redis for state (currently in-memory dict, Redis-ready interface)

---

## Running Locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your LLM key
cp .env.example .env
# Edit .env: set LLM_PROVIDER=groq, LLM_API_KEY=your_key

# 3. Start the bot
uvicorn bot:app --host 0.0.0.0 --port 8080

# 4. Run the judge (in another terminal)
$env:PYTHONIOENCODING="utf-8"; python judge_simulator.py
```

---

## Deploying to Render

1. Push this repo to GitHub (`.env` is git-ignored — key stays local)
2. Go to [render.com](https://render.com) → New → Web Service → connect your repo
3. Render detects the `Dockerfile` automatically
4. Add environment variable in Render dashboard: `LLM_API_KEY = your_groq_key`
5. Deploy — your public URL is the submission URL

> **Note:** Render free tier sleeps after 15 min inactivity. Use a cron service (e.g. UptimeRobot, free) to ping `/v1/healthz` every 10 minutes to keep it warm.

---

## Files

| File | Purpose |
|---|---|
| `bot.py` | FastAPI server — all 5 endpoints + state management |
| `composer.py` | 4-context LLM composition engine + multi-provider support |
| `conversation_handlers.py` | Multi-turn reply logic, intent detection |
| `Dockerfile` | Container for Render/Fly/Cloud Run deployment |
| `render.yaml` | Render deployment config |
| `requirements.txt` | Python dependencies |
| `.env.example` | Config template — copy to `.env` and fill in your key |
