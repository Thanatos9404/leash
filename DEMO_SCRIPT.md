# Demo video script (target 2:45, hard limit 3:00)

**Goal:** show Leash working end to end on the PayPal sandbox, and make three ideas stick: rules you can read and
sign, a PayPal authorization as the hard ceiling, and a receipt for every purchase.

## Before you record

- Use sandbox keys (see KEYS_NEEDED.md) so the PayPal step is the real sandbox approval page. Fixture mode works
  too, but then say "simulated PayPal" on screen.
- Have the sandbox **Personal** account email and password ready, and log in to sandbox.paypal.com once beforehand
  so the approval is quick.
- Start clean: Integration → **Reset everything** (fixture) or delete `var/leash.db` and restart.
- Browser at 1440×900, zoom 100%, bookmarks bar hidden, notifications off. No background music, or a royalty-free
  track you have the rights to. Don't show third-party logos other than the PayPal pages themselves.
- Pre-warm the model: compile one throwaway mandate first, then reset, so the first call isn't slow.

## Script

| Time | Screen | Do | Say (voiceover) |
|---|---|---|---|
| 0:00 | Landing hero | Slow scroll so the product frame straightens | "AI agents can already buy things for us. What's missing is a way to say *only this, only here, never more*, and have it enforced, with proof when something goes wrong. This is Leash." |
| 0:12 | App → New mandate | Click **Coffee restock**, then **Compile the rules** | "I write the rule the way I'd tell a person: up to eighty dollars a month, thirty per order, three stores, never subscriptions, ask me above twenty-five." |
| 0:25 | Review screen | Point at the rule list and the "Drafted by Qwen3" badge | "A model drafts a typed policy, but code checks it: every amount must appear in my words, every store must be a verified merchant I named. These sentences are generated from the rules, so what I read is exactly what's enforced." |
| 0:40 | Review → **Sign mandate** | Sign; show "Signed with this device's key" | "I sign it with an Ed25519 key that never leaves this browser." |
| 0:47 | **Approve allowance on PayPal** → sandbox page | Log in as the sandbox buyer, approve | "The budget becomes a PayPal Orders v2 authorization. PayPal now holds eighty dollars, and that hold is the hard ceiling: the agent can never spend past it." |
| 1:02 | Back on the mandate | Show "Allowance authorized", then click **Restock my usual 1 kg bag…** | "Now the agent shops. It searches, picks a cart, and explains why." |
| 1:12 | Result → **Open receipt and checks** | Scroll the 20 rule checks, the merchant verification, the PayPal capture id; click **Verify receipt** | "Twenty deterministic checks passed. Before any money moved, the merchant verified a signed receipt proving the agent was authorised for this exact cart. Then Leash made a partial capture against the authorization, and chained a receipt to my mandate." |
| 1:32 | Mandate → **A 1 kg bag of Ethiopia Guji from Daily Grind** → Approvals | Show "Ask first", the agent's reasoning, **Approve and sign** | "Twenty-eight dollars is over my ask-first line, so it waits for me. Approving signs this exact cart with my key." |
| 1:47 | **Red team** → **Run all eight** | Let the cards fill; hover Prompt injection and Lookalike store | "Now the attacks. A product page that tells agents I pre-approved five thousand dollars. A lookalike store with the same name on a different domain. A split purchase, a sneaky subscription, a tampered price, a replay. All eight blocked, each with a plain reason, and the model never gets a vote." |
| 2:12 | Header power button → **Stop all agents** | Confirm; show toast and the mandate as Revoked | "If I change my mind, one tap revokes every mandate and voids the PayPal authorization, so the held money is released at PayPal itself." |
| 2:24 | **Audit trail** → **Verify the chain**; then a receipt → **Evidence PDF** | Show "Intact", flash the PDF | "Every step is in a hash-chained log, and any receipt exports as dispute evidence: mandate, reasoning, cart, checks and PayPal ids, which Leash can submit through PayPal's Disputes API." |
| 2:38 | Landing CTA | Hold | "Leash. Let agents shop, and keep them on a leash. Built on the PayPal sandbox; open source." |

**Word count check:** the voiceover is about 330 words, which reads at a natural pace in roughly 2:35-2:45. If you
run long, cut the 1:32 step-up section to one sentence.

## Shot list (for editing)

1. Landing hero, product frame tilt
2. Compose → review → sign
3. Real PayPal sandbox approval
4. Agent purchase → receipt with checks and verification
5. Step-up approval
6. Red team grid, all blocked
7. Kill switch dialog → revoked
8. Audit chain verified, evidence PDF
9. End card with the repo URL

## Upload checklist

- Under 3:00, public on YouTube, link pasted in Devpost.
- Title: "Leash: enforceable spending mandates for AI agents (PayPal AI Hackathon)".
- Description: one paragraph from SUBMISSION.md plus the repo link.
