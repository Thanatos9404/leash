import { ArrowRight, Copy, FileSignature, FileWarning, Fingerprint, Gift, Hand, KeyRound, Power, Repeat, Scale, Scissors, ShieldCheck, Store, Wallet } from "lucide-react";
import {
  Bento,
  BentoCard,
  Button,
  CTASection,
  FeatureSplit,
  FooterCard,
  Hero,
  Marquee,
  Nav,
  Reveal,
  ScrollWords,
  SectionHeader,
  SmoothScroll,
  StatTile,
  Stagger,
  StaggerItem,
  Wordmark,
} from "../kit";
import { DecisionVisual, FlowDiagram, HeroProduct, MandateVisual, ReceiptVisual } from "./visuals";
import { MEASURED } from "./measured";

const THREATS = [
  { icon: <FileWarning size={17} />, t: "Prompt injection", b: "A product page claims you pre-approved $5,000. The policy never reads page text." },
  { icon: <Store size={17} />, t: "Lookalike stores", b: "Merchants are matched by verified identity, not by the name on the page." },
  { icon: <Scissors size={17} />, t: "Split purchases", b: "Two small orders at one store within an hour count as one." },
  { icon: <Repeat size={17} />, t: "Sneaky subscriptions", b: "Recurring plans are refused unless you allowed them." },
  { icon: <Copy size={17} />, t: "Replays", b: "Carts and receipts are single use; PayPal-Request-Id stops double captures." },
  { icon: <Fingerprint size={17} />, t: "Tampering", b: "A changed rule breaks your signature; a changed price fails the merchant check." },
  { icon: <Gift size={17} />, t: "Gift-card cash-outs", b: "Blocked by default in every mandate." },
  { icon: <Wallet size={17} />, t: "Over-capture", b: "PayPal tolerates up to 115% on an authorization. Leash captures 0% beyond it." },
];

export function Landing() {
  return (
    <SmoothScroll>
      <Nav
        brand={<Wordmark name="LEASH" suffix=".pay" />}
        links={[
          { label: "How it works", href: "#how" },
          { label: "Safety", href: "#safety" },
          { label: "PayPal", href: "#paypal" },
          { label: "Results", href: "#results" },
        ]}
        cta={{ label: "Open the app", href: "/app" }}
      />
      <Hero
        eyebrow={
          <>
            <ShieldCheck size={14} strokeWidth={1.8} className="text-accent" /> Spending mandates for AI agents · PayPal AI Hackathon
          </>
        }
        title="Let agents shop. *Keep* them on a leash."
        subtitle="Write the rules in plain words. Leash signs them, checks every cart, and settles only what you allowed on PayPal."
        primary={{ label: "Open the app", href: "/app" }}
        secondary={{ label: "How it works", href: "#how" }}
        product={<HeroProduct />}
      />

      <section className="section-y !pb-0">
        <div className="container-x">
          <p className="t-eyebrow mb-6 text-center">Built with</p>
          <Marquee items={["PayPal Orders v2", "Payments v2 authorizations", "PayPal Disputes API", "PayPal webhooks", "Nebius Token Factory", "Qwen3 30B", "Gemma 3 27B", "Ed25519 JWS", "WebCrypto", "FastAPI", "React", "Channel3-ready search"]} />
        </div>
      </section>

      <section className="section-y">
        <div className="container-x">
          <ScrollWords text="AI agents can already pay. What they can't do yet is *prove* they were allowed to." />
        </div>
      </section>

      <section id="paypal" className="section-y !pt-0">
        <div className="container-x space-y-28">
          <FeatureSplit
            eyebrow="Rules"
            title="Rules you can read, and sign."
            body="Describe a mandate the way you'd brief a person. A model drafts the policy; code throws out any amount or store you never wrote. You sign the plain-language version, which is generated from the rules themselves."
            bullets={[
              { icon: <FileSignature size={16} />, label: "Ed25519 signature" },
              { icon: <KeyRound size={16} />, label: "Key stays on device" },
              { icon: <Scale size={16} />, label: "Typed, deterministic" },
              { icon: <Hand size={16} />, label: "Ask-first lines" },
            ]}
            visual={<MandateVisual />}
          />
          <FeatureSplit
            flip
            eyebrow="Checks"
            title="Every cart is checked. The model can't argue."
            body="Merchant identity, price, category, subscriptions, budget, velocity, split orders and replays: each cart runs through the same deterministic rules. The agent's reasoning is recorded, never consulted."
            visual={<DecisionVisual />}
          />
          <FeatureSplit
            eyebrow="Money"
            title="PayPal holds the money. Leash holds the receipts."
            body="Your allowance is a PayPal authorization you approve once. Each allowed purchase is a partial capture, made only after the merchant verifies Leash's signed receipt. Revoke, and the authorization is voided."
            action={
              <Button href="/app" icon={<ArrowRight size={16} />}>
                See it in the app
              </Button>
            }
            visual={<ReceiptVisual />}
          />
        </div>
      </section>

      <section id="how" className="section-y bg-paper">
        <div className="container-x">
          <SectionHeader eyebrow="How it works" title="Five steps, one leash" lead="The agent proposes. Your rules decide. PayPal settles. Everything is signed and chained." />
          <Bento className="mt-14">
            <BentoCard span={7} icon={<FileSignature size={18} />} title="1. Say it" body="Plain words in, a typed policy out. Every number must appear in what you wrote; every store must be a verified merchant you named." visual={<FlowDiagram />} />
            <BentoCard span={5} icon={<KeyRound size={18} />} title="2. Sign it" body="Your browser signs the rules hash with an Ed25519 key it never exports. Change one rule and the signature stops verifying." />
            <BentoCard span={4} icon={<Wallet size={18} />} title="3. Fund it" body="One PayPal approval creates an AUTHORIZE hold for the budget. That hold is the hard ceiling." />
            <BentoCard span={4} icon={<ShieldCheck size={18} />} title="4. Shop" body="Allowed carts are captured after the merchant verifies the receipt. Others ask you, or are blocked with reasons." />
            <BentoCard span={4} icon={<Power size={18} />} title="5. Stop it" body="Revoke one mandate or hit the kill switch. Leash voids the authorization at PayPal, releasing the funds." />
          </Bento>
        </div>
      </section>

      <section id="safety" className="section-y !pt-0">
        <div className="container-x">
          <SectionHeader eyebrow="Threat model" title="What it stops" lead="Each of these has an automated test, and a button on the app's red-team page." />
          <Stagger className="mt-12 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {THREATS.map((x) => (
              <StaggerItem key={x.t} className="paper-card p-5">
                <span className="grid size-9 place-items-center rounded-full bg-surface text-accent shadow-[var(--shadow-soft)]">{x.icon}</span>
                <p className="mt-4 font-display text-[15.5px] font-semibold text-ink">{x.t}</p>
                <p className="mt-1.5 text-[14px] leading-relaxed text-ink-2">{x.b}</p>
              </StaggerItem>
            ))}
          </Stagger>
        </div>
      </section>

      <section id="results" className="section-y !pt-0">
        <div className="container-x">
          <SectionHeader eyebrow="Results" title="Measured, not promised" align="left" />
          <Reveal className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {MEASURED.map((m) => (
              <StatTile key={m.label} icon={m.icon} label={m.label} value={m.value} delta={m.delta} tone={m.tone} hint={m.hint} />
            ))}
          </Reveal>
        </div>
      </section>

      <CTASection
        title={
          <>
            Give your agent a <span className="t-serif">leash</span>.
          </>
        }
        lead="Runs on the PayPal sandbox. No real money moves."
        action={
          <Button href="/app" size="lg" icon={<ArrowRight size={17} />}>
            Open the app
          </Button>
        }
      />
      <FooterCard
        brand={<Wordmark name="LEASH" suffix=".pay" />}
        blurb="Signed, enforceable spending mandates for AI shopping agents, settled on PayPal. Built by Yashvardhan Thanvi for the PayPal AI Hackathon 2026."
        columns={[
          { title: "Product", links: [{ label: "Open the app", href: "/app" }, { label: "How it works", href: "#how" }, { label: "Threat model", href: "#safety" }] },
          { title: "Project", links: [{ label: "Source on GitHub", href: "https://github.com/Thanatos9404" }, { label: "API reference", href: "/docs" }, { label: "Public keys (JWKS)", href: "/.well-known/jwks.json" }] },
        ]}
        legal={<span>MIT licence. PayPal sandbox only; not affiliated with or endorsed by PayPal. Demo storefronts are fictional. Fonts are OFL; no stock imagery.</span>}
      />
    </SmoothScroll>
  );
}
