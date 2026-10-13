# Leash

**Enforceable spending mandates for AI shopping agents, settled on PayPal.**

Write what your agent may buy in plain words. Leash compiles it into a typed policy you can read and sign, has
PayPal hold the budget as an authorization, checks every cart the agent proposes against the signed rules, has
the merchant verify the agent's authority before any capture, and leaves a signed receipt chain you can hand to a
dispute. PayPal sandbox only: no real money moves.

![Leash landing page](docs/screenshots/01-landing-hero.png)

| | |
|---|---|
| ![Review a mandate](docs/screenshots/11-review.png) | ![Red team: eight attacks blocked](docs/screenshots/21-redteam.png) |
| ![Step-up approval](docs/screenshots/20-approval.png) | ![Receipt with checks and dispute](docs/screenshots/24-receipt-detail.png) |

## Why

Agents that pay are arriving fast, and PayPal is in the middle of it:

- PayPal rolled out MCP servers ([Apr 2, 2025](https://developer.paypal.com/community/blog/paypal-model-context-protocol/))
  and the Agent Toolkit ([Apr 14, 2025](https://developer.paypal.com/community/blog/paypal-agentic-ai-toolkit/)),
  partnered with Perplexity ([May 14, 2025](https://newsroom.paypal-corp.com/2025-05-14-Perplexity-Selects-PayPal-to-Power-Agentic-Commerce)),
  announced support for Google's Agent Payments Protocol ([Sep 16, 2025](https://developer.paypal.com/community/blog/PayPal-Agent-Payments-Protocol/)),
  and on Oct 28, 2025 launched [agentic commerce services](https://newsroom.paypal-corp.com/2025-10-28-PayPal-Launches-Agentic-Commerce-Services-to-Power-AI-Driven-Shopping)
  and [adopted the Agentic Commerce Protocol for ChatGPT](https://newsroom.paypal-corp.com/2025-10-28-OpenAI-and-PayPal-Team-Up-to-Power-Instant-Checkout-and-Agentic-Commerce-in-ChatGPT).
- Stripe and OpenAI released the Agentic Commerce Protocol with Instant Checkout in ChatGPT
  ([Sep 29, 2025](https://stripe.com/newsroom/news/stripe-openai-instant-checkout)); Google published AP2
  ([Sep 2025](https://cloud.google.com/blog/products/ai-machine-learning/announcing-agents-to-payments-ap2-protocol));
  Visa ([Intelligent Commerce, Apr 30, 2025](https://usa.visa.com/about-visa/newsroom/press-releases.releaseId.21366.html);
  [Trusted Agent Protocol, Oct 14, 2025](https://usa.visa.com/about-visa/newsroom/press-releases.releaseId.21716.html))
  and Mastercard ([Agent Pay, Apr 29, 2025](https://newsroom.mastercard.com/news/press/2025/april/mastercard-unveils-agent-pay-pioneering-agentic-payments-technology-to-power-commerce-in-the-age-of-ai))
  built rails for agent payments.
- People aren't sure yet. In Riskified's Q1 2026 survey of 2,000 US and UK consumers, 55% were not comfortable with
  AI agents making purchases for them; in an ACI Worldwide / YouGov survey of UK adults (June 2026), only 43%
  trusted agents to stay within spending caps
  ([Digital Transactions, Apr 2026](https://www.digitaltransactions.net/many-consumers-say-no-thanks-to-agent-based-payments/);
  [Digital Transactions, Jun 2026](https://www.digitaltransactions.net/a-survey-shows-a-need-to-bolster-trust-in-agentic-commerce/)).

The gap Leash fills is **trust and accountability at the moment of payment**:

- A person can't say "restock my coffee, max $30 an order, only these three shops, never a subscription" and know
  the agent *can't* overspend, rather than hoping it won't.
- A merchant can't tell whether an agent was actually authorised to place this order.
- When something goes wrong, there is no evidence trail that ties the person's instruction to the agent's choice
  and to the payment.

## What it does

1. **Say it.** The user writes a mandate in plain language. A model (Nebius Token Factory) drafts a typed policy.
   Deterministic code then checks the draft: every amount must appear in the user's text, a number the text gives
   one meaning (say "ask me above $50") can't be reused for another ("max $50 per order"), and every store must
   be a verified merchant the user actually named. The policy is rendered back to plain language **by code, not by
   the model**, so the sentences the user signs are exactly the rules that are enforced.
2. **Sign it.** The browser signs the policy hash with an Ed25519 key created by WebCrypto and kept in IndexedDB
   (non-extractable). Browsers without Ed25519 fall back to a clearly labelled server-held demo key.
3. **Fund it.** The budget becomes a **PayPal Orders v2 order with `intent: AUTHORIZE`** that the user approves
   once. That authorization is the hard ceiling.
4. **Shop.** The shopping agent (or any MCP client) searches products and proposes a cart. The policy engine runs
   about 20 deterministic checks (merchant identity, price, category, subscriptions, quantity, per-purchase cap,
   budget, remaining PayPal allowance, velocity, split orders, order count, ask-first line, replay, signature,
   validity window, kill switch) and answers **allow**, **ask** or **deny**.
   - *allow*: Leash issues a signed authorization receipt, the merchant verifies it (`POST /v1/verify`), then Leash
     makes a **partial capture** against the authorization (`final_capture: false`) and issues a settlement
     receipt chained to the previous one.
   - *ask*: the user approves by signing that exact cart.
   - *deny*: blocked with reasons. For waivable rules the human can allow a one-off, which is its own
     `AUTHORIZE` order the user approves on PayPal; it is voided if verification fails or the mandate is revoked.
     Spoofed merchants, tampered carts and replays can never be waived.
5. **Stop it.** Revoking a mandate, or the global kill switch, **voids the PayPal authorization**, releasing the held
   funds at PayPal itself.
6. **Prove it.** Every purchase has an intent receipt (compact JWS, Ed25519) that binds the signed mandate, the
   agent's reasoning, the cart hash, the policy decision, the approval and the PayPal order, authorization and
   capture ids. It exports as JSON and as a PDF, and Leash can attach it to a PayPal dispute through the Disputes
   API. A hash-chained audit ledger records every step.

## Why PayPal is central

PayPal isn't a checkout button bolted on at the end: the allowance *is* a PayPal authorization, and every money
movement is a PayPal operation gated by the policy engine.

| PayPal API | Endpoint | What Leash uses it for |
|---|---|---|
| OAuth 2.0 | `POST /v1/oauth2/token` | Client-credentials token, cached and refreshed on 401 |
| Orders v2 | `POST /v2/checkout/orders` (`intent: AUTHORIZE`) | The mandate's allowance, and one-off exception carts |
| Orders v2 | `GET /v2/checkout/orders/{id}`, `POST .../authorize` | Confirm buyer approval, then place the authorization |
| Payments v2 | `POST /v2/payments/authorizations/{id}/capture` | One partial capture per allowed purchase (`final_capture: false`), with `invoice_id` = purchase id |
| Payments v2 | `POST /v2/payments/authorizations/{id}/void` | Revoke, kill switch, failed merchant verification |
| Payments v2 | `GET /v2/payments/authorizations/{id}` | Authorization status sync |
| Payments v2 | `POST /v2/payments/captures/{id}/refund` | Refunds |
| Disputes v1 | `GET /v1/customer/disputes`, `GET .../{id}` | Link buyer disputes to agent purchases by capture id |
| Disputes v1 | `POST /v1/customer/disputes/{id}/provide-evidence` | Submit the signed receipt PDF and an evidence statement |
| Webhooks | `POST /v1/notifications/verify-webhook-signature` | Verify every incoming event before acting on it |

Details that matter for safety: every write sends a deterministic `PayPal-Request-Id` (retries can't double-charge);
captures carry a unique `invoice_id` (PayPal refuses duplicates); and Leash never captures beyond the authorized
amount even though PayPal itself accepts up to 115% (or +$75) on an authorization.

PayPal's agentic tooling: the [PayPal Agent Toolkit](https://github.com/paypal/agent-toolkit) and MCP server give
a model direct payment tools. Leash takes the opposite position on purpose: the model gets **shopping** tools only
(see `leash/mcp_server.py`, which exposes Leash itself as an MCP server), and only the policy engine can reach the
PayPal APIs. The demo merchants' carts follow the shape of the `PayPalCart` object in PayPal's agentic commerce Cart
API (limited release), which Leash reads but does not call.

## Where AI is used, and where it isn't

| Task | Model (Nebius Token Factory) | Guardrail |
|---|---|---|
| Compile plain language into a policy | `Qwen/Qwen3-30B-A3B-Instruct-2507` | Numbers must be grounded in the user's text; stores must be named verified merchants; deterministic parser as fallback; plain language rendered by code; the user reviews and signs |
| Shopping agent: search query and product choice with reasoning | `Qwen/Qwen3-30B-A3B-Instruct-2507` | Product text is wrapped as untrusted data; whatever it picks goes through the policy engine |
| Dispute evidence statement | `google/gemma-3-27b-it` | Must contain the real amount and merchant, otherwise a factual template is used; the signed receipt is attached either way |
| Allow / ask / deny, amounts, captures, voids | **None** | Deterministic code only |

Without `NEBIUS_API_KEY`, every model step falls back to deterministic rules and the UI says so.

## Architecture

```mermaid
flowchart LR
  U[User<br/>browser key] -->|plain words| C[Mandate compiler<br/>LLM + grounding checks]
  C -->|typed policy + plain language| U
  U -->|Ed25519 signature| M[(Signed mandate)]
  M -->|budget| PPA[PayPal Orders v2<br/>AUTHORIZE allowance]
  A[Shopping agent<br/>or any MCP client] -->|search| S[Catalog<br/>fixture or Channel3]
  A -->|proposed cart| P{Policy engine<br/>deterministic}
  M --> P
  P -->|deny| B[Blocked + reasons]
  P -->|ask| U
  P -->|allow| R[Authorization receipt<br/>JWS]
  R --> V[Merchant verifies<br/>/v1/verify]
  V -->|ok| CAP[PayPal partial capture<br/>Payments v2]
  CAP --> SR[Settlement receipt<br/>chained]
  SR --> D[PayPal Disputes API<br/>evidence]
  U -->|revoke / kill switch| VOID[PayPal void]
  P & CAP & VOID --> L[(Hash-chained ledger)]
```

```mermaid
sequenceDiagram
  participant U as User
  participant L as Leash
  participant PP as PayPal sandbox
  participant A as Agent
  participant Mer as Merchant
  U->>L: sign mandate (Ed25519)
  L->>PP: POST /v2/checkout/orders (AUTHORIZE, budget)
  U->>PP: approve once
  L->>PP: POST /v2/checkout/orders/{id}/authorize
  A->>L: proposed cart
  L->>L: 20 deterministic checks
  L->>Mer: signed authorization receipt
  Mer->>L: POST /v1/verify (single use)
  L->>PP: POST /v2/payments/authorizations/{id}/capture (final_capture=false)
  L-->>U: settlement receipt (chained)
  U->>L: revoke
  L->>PP: POST /v2/payments/authorizations/{id}/void
```

## Safety model

Summary below; the full threat model with a test per threat is in [THREAT_MODEL.md](THREAT_MODEL.md).

- **Models propose, code decides.** Nothing a model writes can change an amount, a payee or a permission.
- **Signed intent.** The policy hash is signed by the user's key and re-verified on every decision.
- **PayPal as the ceiling.** The allowance is an authorization; revoking voids it at PayPal.
- **Merchant verification before capture.** Receipts are short-lived, single use and bound to one merchant and cart.
- **Idempotency.** Deterministic `PayPal-Request-Id`, unique `invoice_id` per capture, per-mandate locks.
- **Human in the loop.** Ask-first lines, signed cart approvals, PayPal-approved one-offs, a kill switch.
- **Sandbox lock.** There is no live PayPal host in the code; `PAYPAL_ENV` other than `sandbox` stops the app.

## Run it

Requirements: Python 3.11+. Node 18+ only if you want to rebuild the frontend (a build is committed).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn leash.app:app --port 8000
# open http://localhost:8000  (landing page)  and  http://localhost:8000/app  (the product)
```

With no `.env`, Leash runs in **fixture mode**: PayPal is simulated in-process with documented response shapes,
ids prefixed `FX`, and a PayPal approval page that is clearly labelled as a simulation. Every screen shows which mode
is active.

- **Real PayPal sandbox:** add `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` (see [KEYS_NEEDED.md](KEYS_NEEDED.md)),
  then `python scripts/sandbox_smoke.py` to check them.
- **Models:** add `NEBIUS_API_KEY` (or `LEASH_EXTRA_ENV_FILE` pointing at a `.env` that has it).
- **Screenshots:** `python scripts/screenshots.py http://localhost:8000 docs/screenshots` drives the whole UI in
  headless Chromium (needs `pip install playwright`).

A two-minute tour for judges: open `/app` → **New mandate** → *Coffee restock* → **Compile** → **Sign** →
**Approve allowance on PayPal** → approve → **Restock my usual…** → open the receipt → **Red team → Run all eight** →
power button (kill switch).

### Frontend development

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173, proxies API calls to http://localhost:8000
npm run build      # writes frontend/dist, served by FastAPI
```

The UI uses the shared hackathon design kit (`frontend/src/kit`, React 18, Tailwind v4, Motion, Lenis).

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

All tests run offline in fixture mode: mandate compiler and grounding, crypto, the policy engine, every threat in
the threat model, the full lifecycle (allowance, partial captures, step-up, exceptions, refunds, dispute evidence,
revoke), the HTTP API, and the real sandbox REST client against a mock transport (headers, idempotency keys,
retries, multipart evidence, webhook verification). `python scripts/measure.py` regenerates the numbers shown on
the landing page from a real run.

### MCP

```bash
pip install "mcp>=2"
python -m leash.mcp_server   # stdio; tools: list_mandates, search_products, checkout
```

## Deploy

`render.yaml` (Render free plan) and a `Dockerfile` are included; nothing has been deployed. See KEYS_NEEDED.md.

## Project layout

```
leash/            FastAPI app and engine
  compiler.py     plain language -> typed policy, grounding checks, plain-language rendering
  policy.py       deterministic allow / ask / deny
  engine.py       mandates, allowance, checkout, merchant verification, captures, exceptions, disputes
  paypal/         sandbox REST client and the labelled fixture simulator
  receipts.py     intent receipts (JWS) and evidence PDF
  crypto.py       Ed25519 keys, compact JWS
  agent.py        shopping agent and red-team scenarios
  mcp_server.py   Leash as an MCP server
frontend/         React app (landing page + product), built into frontend/dist
data/catalog.json fictional demo storefronts, including a lookalike store and an injected product page
tests/            pytest suite
```

## Honest limitations

- One demo owner, no login. Production needs accounts and passkey-backed device keys.
- The verified-merchant list and storefronts are fixtures; in the demo every storefront settles to one sandbox
  merchant account.
- PayPal authorizations last up to 29 days, so mandates are capped at 28 days and need renewal.
- Leash borrows AP2's ideas but does not implement the AP2 protocol or its credential formats. In AP2 v0.1 terms, a
  Leash mandate is like an Intent Mandate (signed in advance, human not present) and a signed cart approval is like
  a Cart Mandate (human present); the current AP2 spec describes these as open and closed mandates
  ([spec](https://ap2-protocol.org/ap2/specification/)).
- Fixture mode imitates PayPal's documented behaviour for the paths used here; it is not PayPal.

## Credits and licence

MIT licence (see `LICENSE`). Fonts are OFL (Open Runde, Inter, Instrument Serif, Fragment Mono, Silkscreen),
self-hosted. Icons: Lucide. No stock imagery. Demo merchants and products are fictional. Not affiliated with or
endorsed by PayPal. Built by Yashvardhan Thanvi for the PayPal AI Hackathon 2026.
