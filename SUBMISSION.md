# Devpost submission draft: Leash

Copy each section into the matching Devpost field. Replace the bracketed links before submitting.

**Project name:** Leash

**Tagline (≤ 200 chars):** Enforceable spending mandates for AI shopping agents: signed rules you can read, a PayPal
authorization as the hard ceiling, and a signed receipt for every purchase.

**Links**
- Repository: [GitHub URL, public, MIT licence visible in About]
- Demo: [Render URL] or run locally in two commands (README, "Run it")
- Video: [YouTube URL, public, under 3 minutes]

## Inspiration

Agents that buy things for people went from demos to real rails in 2025: PayPal's Agent Toolkit and MCP servers,
Perplexity checkout, PayPal's support for Google's AP2, the Agentic Commerce Protocol in ChatGPT, Visa Intelligent
Commerce and Mastercard Agent Pay (sources in the README). What nobody has made easy is the boring, essential part:
telling an agent "restock my coffee, max $30 an order, only these three shops, never a subscription, ask me above
$25" and *knowing* it can't do anything else. Surveys say people aren't there yet: in one UK survey only 43%
trusted an agent to stay within a spending cap. Leash is our answer: the agent can propose anything, but only
what you signed can be paid.

## What it does

- **Plain words in, signed rules out.** You describe the mandate; a model drafts a typed policy; code rejects any
  amount or store you didn't write; the rules are rendered back to plain language by code (not by the model) and you
  sign that with an Ed25519 key created in your browser.
- **PayPal holds the budget.** Funding a mandate creates a PayPal Orders v2 order with `intent: AUTHORIZE`, approved
  once. That authorization is the hard ceiling.
- **Every cart is checked, deterministically.** About 20 rules: merchant identity (lookalike detection), price,
  category, subscriptions, quantity, per-purchase cap, budget, remaining allowance, velocity, split orders, order
  count, ask-first line, replay, signature, dates and kill switch. The outcome is allow, ask or deny, with reasons
  in plain language. The model never gets a vote.
- **Merchant verification before capture.** Allowed carts get a short-lived, single-use signed receipt; the merchant
  verifies it (`/v1/verify`), then Leash makes a partial capture against the authorization.
- **Human in the loop where it matters.** Carts above your ask-first line wait for a signed approval; hard-rule
  violations can only be waived as a one-off you approve on PayPal (never for spoofed stores, tampered carts or
  replays).
- **One tap to stop.** Revoke or the global kill switch voids the PayPal authorization.
- **Proof.** Every purchase has a chained intent receipt (JWS) linking your mandate, the agent's reasoning, the
  cart, the checks and the PayPal ids. It exports as JSON and PDF and can be submitted to a PayPal dispute through
  the Disputes API. Every step is in a hash-chained audit trail.
- **Red team built in.** Eight attacks (prompt injection on a product page, lookalike store, split purchase,
  sneaky subscription, over-cap, gift-card cash-out, tampered price, replay) run through the real checkout path,
  and all are blocked.
- **Works with other agents.** Leash is also an MCP server that gives any agent shopping tools, never payment tools.

## How we built it

- **Backend:** Python, FastAPI, Pydantic, SQLite. A deterministic policy engine, an Ed25519/JWS module
  (`cryptography`), a hash-chained ledger, and a PayPal REST client with OAuth caching, `PayPal-Request-Id` on every
  write, retries under the same id, and webhook verification.
- **PayPal (sandbox):** Orders v2 (create with `AUTHORIZE`, get, authorize), Payments v2 (capture authorization with
  `final_capture: false`, void, get authorization, refund), Disputes v1 (list, get, provide-evidence with a PDF),
  webhook signature verification and OAuth. Demo carts follow the `PayPalCart` shape from PayPal's agentic
  commerce Cart API.
- **AI:** Nebius Token Factory (OpenAI-compatible): `Qwen/Qwen3-30B-A3B-Instruct-2507` compiles mandates and runs
  the shopping agent; `google/gemma-3-27b-it` drafts dispute statements. Every model step has a deterministic
  fallback, and no model output reaches money paths.
- **Frontend:** React 18, TypeScript, Tailwind v4, Motion and Lenis on a shared design kit; WebCrypto Ed25519 for
  device signing.
- **Testing:** 55 offline pytest tests, including one or more per threat in THREAT_MODEL.md and the real REST
  client against a mock PayPal transport. A labelled fixture simulator mirrors the documented PayPal responses so
  the whole product runs without keys.

## AI tools used (required field)

| Tool | Where | How it is used |
|---|---|---|
| Nebius Token Factory, `Qwen/Qwen3-30B-A3B-Instruct-2507` | Runtime | Turns a plain-language mandate into a JSON policy draft (then grounded and validated by code); generates the agent's search query and picks a product with a short rationale |
| Nebius Token Factory, `google/gemma-3-27b-it` | Runtime | Drafts a factual dispute statement from signed receipt facts (checked for the real amount and merchant, else a template is used) |
| Claude Code (Anthropic) | Development | Coding assistant used while building and testing the project; it is not part of the running product |

## Challenges we ran into

- **Keeping the model away from the money without making it useless.** The answer was a strict split: the model
  drafts and explains; code grounds every number and store, renders the plain language, and makes every decision.
  We found and fixed a subtle failure: the model read "ask me above $50" as a $50 per-order cap, so the compiler now
  refuses to reuse a number the text already gave another meaning.
- **PayPal is more lenient than a mandate.** PayPal accepts captures up to 115% of an authorization (or +$75), so
  Leash checks the remaining allowance itself before every capture.
- **Authorization lifetime.** Authorizations last up to 29 days with a 3-day honor period, which shaped mandates into
  28-day allowances that renew.
- **Testing payments without keys.** We built a fixture simulator that reproduces PayPal's documented shapes and the
  rules our safety tests depend on (approval before authorization, partial captures, void after partial capture,
  duplicate `invoice_id`, request-id replay) and labels itself everywhere.

## Accomplishments we're proud of

- Eight of eight red-team attacks blocked through the same path a real agent uses, each with a plain reason.
- A revoke that releases funds at PayPal itself, not just in our database.
- Receipts a merchant can verify offline with a public key, and that export as dispute evidence.

## What we learned

Agentic commerce safety is mostly unglamorous engineering: identity by id not name, idempotency keys,
single-use nonces, signatures over hashes, and a clear line between what a model may suggest and what code
decides.

## What's next

Passkey-backed signing and real accounts; per-merchant authorizations or PayPal vaulting for merchants that
integrate directly; publishing the receipt format and verify endpoint as a small open spec; and mapping mandates
onto AP2's open and closed mandates.

## Built with

python, fastapi, pydantic, sqlite, cryptography, paypal-orders-v2, paypal-payments-v2, paypal-disputes,
paypal-webhooks, nebius-token-factory, qwen, gemma, react, typescript, tailwindcss, motion, mcp, render

## Testing instructions for judges

1. `pip install -r requirements.txt` then `uvicorn leash.app:app --port 8000`, open <http://localhost:8000/app>.
   No keys needed: PayPal runs in a labelled fixture mode and agents use offline rules.
2. New mandate → Coffee restock → Compile → Sign → Approve allowance on PayPal → Approve (simulated) →
   Restock my usual… → open the receipt → Red team → Run all eight → power button (kill switch).
3. Sandbox mode: add sandbox keys to `.env` (KEYS_NEEDED.md); the PayPal step becomes the real sandbox approval page.
4. `pip install -r requirements-dev.txt && pytest` runs every test offline.
