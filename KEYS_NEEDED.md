# Keys and accounts you need to create yourself

Leash runs fully without any keys (fixture mode: PayPal is simulated in-process and labelled as such; agents fall
back to offline rules). To demo against the **real PayPal sandbox**, do the steps below. Never use live
credentials: the app refuses any `PAYPAL_ENV` other than `sandbox`.

Put every value in a `.env` file at the project root (copy `.env.example`). `.env` is git-ignored.

## 1. PayPal developer account and sandbox REST app (required for sandbox mode, ~10 minutes)

1. Go to <https://developer.paypal.com> and log in with any PayPal account, or create a free one. This also creates
   two sandbox test accounts for you: a **Business** account (the merchant) and a **Personal** account (the buyer).
2. Open **Apps & Credentials**, make sure the **Sandbox** toggle is selected, and click **Create App**.
   - Name: `Leash` · Type: **Merchant** · Sandbox business account: the default one.
3. Copy the **Client ID** and **Secret** into `.env`:
   ```
   PAYPAL_CLIENT_ID=...
   PAYPAL_CLIENT_SECRET=...
   PAYPAL_ENV=sandbox
   ```
4. In the app's settings, under features, keep **Accept payments** on (Orders v2 with `AUTHORIZE`). If a
   **Disputes** option is listed, enable it. Leash does not need Payouts, Invoicing or Vault.
5. Check the keys: `python scripts/sandbox_smoke.py` should print "OAuth token: ok" and a sandbox approval link.

### Sandbox accounts for the demo

- **Testing Tools → Sandbox Accounts** lists the generated accounts. Open the **Personal** account's
  **View/Edit account** to see its email and password. You log in with it on sandbox.paypal.com when Leash sends
  you to PayPal to approve an allowance. Optionally put its email in `.env` as `LEASH_BUYER_EMAIL`.
- The Business account is the merchant that receives captures. Log in to <https://www.sandbox.paypal.com> with it
  to see authorizations, captures and refunds arrive.

### Return URL

PayPal sends the user back to `LEASH_PUBLIC_BASE_URL` after approval. Locally that is `http://localhost:8000`
(the default). When deployed, set it to the public URL (Render sets `RENDER_EXTERNAL_URL` automatically, which
Leash uses).

## 2. Webhooks (optional, needs a public URL)

Leash works without webhooks (it polls PayPal when you press "I've approved" and on page loads). For push updates:

1. Deploy first (section 5) so PayPal can reach `https://<your-app>/webhooks/paypal`.
2. In your sandbox app, **Add Webhook** with that URL and these events: `CHECKOUT.ORDER.APPROVED`,
   `PAYMENT.AUTHORIZATION.CREATED`, `PAYMENT.AUTHORIZATION.VOIDED`, `PAYMENT.CAPTURE.COMPLETED`,
   `PAYMENT.CAPTURE.REFUNDED`, `CUSTOMER.DISPUTE.CREATED`, `CUSTOMER.DISPUTE.UPDATED`, `CUSTOMER.DISPUTE.RESOLVED`.
3. Copy the **Webhook ID** into `PAYPAL_WEBHOOK_ID`. Leash verifies every event with PayPal's
   `verify-webhook-signature` API and rejects anything that fails.

## 3. Disputes in the sandbox (optional)

In fixture mode the app has a "Simulate a buyer dispute" button. In sandbox mode, log in to
<https://www.sandbox.paypal.com> as the **Personal** account, open the transaction from the Leash capture and
report a problem (Resolution Center). Then in Leash use **Disputes sync** (the receipt page links the dispute by
capture id) and **Respond with the signed receipt**, which calls `provide-evidence` with the evidence PDF.

## 4. AI model key (optional)

Leash uses Nebius Token Factory (OpenAI-compatible API). Without a key, the compiler, agent and dispute writer use
deterministic offline fallbacks, labelled "rules" in the UI.

- Create a key at <https://tokenfactory.nebius.com> and set `NEBIUS_API_KEY=...`, or point
  `LEASH_EXTRA_ENV_FILE` at another `.env` that already contains `NEBIUS_API_KEY` (only that one key is read).
- Default models: `Qwen/Qwen3-30B-A3B-Instruct-2507` (compiler and agent), `google/gemma-3-27b-it` (dispute
  statements). Override with `LEASH_LLM_MODEL`, `LEASH_LLM_WRITER_MODEL`.

## 5. Channel3 product search (optional, for the Channel3 sponsor prize)

Without a key, the agent shops the fictional demo storefronts in `data/catalog.json`. With one, it searches
Channel3's catalog (`POST https://api.trychannel3.com/v1/search`, header `x-api-key`) and treats each offer's
domain as the merchant. Purchases still settle in the PayPal sandbox; nothing is bought from real retailers.

- Sign up as a developer at <https://trychannel3.com/sign-up/developer> (their docs state 1,000 free searches a
  month) and set `CHANNEL3_API_KEY=...`.
- Note: the coffee mandate's allow-list names the demo storefronts, so for Channel3 results write a mandate that
  allows "any verified store" or names the retailer domains you want.

## 6. Deploy to Render (free plan, optional)

`render.yaml` is ready but nothing has been deployed.

1. Push the repository to GitHub (make it public when you are ready to submit).
2. In Render: **New → Blueprint**, pick the repo; Render reads `render.yaml`.
3. Fill the secret env vars it asks for: `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, optionally
   `PAYPAL_WEBHOOK_ID`, `NEBIUS_API_KEY`, `CHANNEL3_API_KEY`.
4. After the first deploy, open `https://<service>.onrender.com`. Free instances sleep when idle (the first request
   takes ~30-60 s) and their disk is ephemeral, so demo data resets on restart.

## Checklist

| Item | Needed for | Status |
|---|---|---|
| PayPal developer account + sandbox app | Real sandbox calls | You |
| Sandbox personal account login | Approving allowances on PayPal | Auto-created by PayPal |
| `PAYPAL_WEBHOOK_ID` | Push updates (optional) | After deploy |
| `NEBIUS_API_KEY` | Model-written policies and choices | Already in your other project's `.env` |
| `CHANNEL3_API_KEY` | Real product search (optional) | You |
| Render account | Hosted demo URL (optional) | You |
