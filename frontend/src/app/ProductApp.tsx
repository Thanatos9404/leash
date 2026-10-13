import { AnimatePresence, motion } from "motion/react";
import { Activity, FileCheck2, Gauge, Hand, Plug, Power, RefreshCw, ScrollText, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { AppShell, Button, IconButton, SearchPill, Wordmark } from "../kit";
import { cn } from "../kit/lib/cn";
import { EASE } from "../kit/lib/motion";
import { api } from "../lib/api";
import { money } from "../lib/format";
import { linkTo, navigate, useLocation } from "../lib/router";
import { ActivityScreen } from "./screens/ActivityScreen";
import { ApprovalsScreen } from "./screens/ApprovalsScreen";
import { IntegrationScreen } from "./screens/IntegrationScreen";
import { MandateScreen, MandatesScreen } from "./screens/MandatesScreen";
import { OverviewScreen } from "./screens/OverviewScreen";
import { PurchaseScreen, ReceiptsScreen } from "./screens/ReceiptsScreen";
import { RedTeamScreen } from "./screens/RedTeamScreen";
import { LeashProvider, useLeash } from "./store";
import { BusyButton, ErrorState, Toasts } from "./ui";

const NAV = [
  { id: "overview", label: "Overview", icon: <Gauge />, path: "/app" },
  { id: "mandates", label: "Mandates", icon: <ScrollText />, path: "/app/mandates" },
  { id: "approvals", label: "Approvals", icon: <Hand />, path: "/app/approvals" },
  { id: "receipts", label: "Receipts", icon: <FileCheck2 />, path: "/app/receipts" },
  { id: "activity", label: "Audit trail", icon: <Activity />, path: "/app/activity" },
];
const TOOLS = [
  { id: "redteam", label: "Red team", icon: <ShieldAlert />, path: "/app/redteam" },
  { id: "integration", label: "Integration", icon: <Plug />, path: "/app/integration" },
];

export function ProductApp() {
  return (
    <LeashProvider>
      <Shell />
      <Toasts />
    </LeashProvider>
  );
}

function Shell() {
  const { path, search } = useLocation();
  const { dash, status, error, refresh, toast } = useLeash();
  const [confirmKill, setConfirmKill] = useState(false);
  const parts = path.replace(/\/+$/, "").split("/").slice(2); // after /app
  const section = parts[0] ?? "overview";
  const active = section === "purchases" ? "receipts" : section;

  useEffect(() => {
    const q = new URLSearchParams(search);
    if (q.get("funded")) toast("PayPal approved the allowance. The mandate is live.");
    if (q.get("approved")) toast("Approved on PayPal. The merchant verified the agent and the payment was captured.");
    if (q.get("cancelled")) toast("You left PayPal without approving. Nothing was charged.", "attention");
    if (q.get("error")) toast(q.get("error")!, "attention");
    if (q.toString()) history.replaceState(null, "", path);
  }, [search, path, toast]);

  const pending = dash?.stats.pending ?? 0;
  const items = NAV.map((n) => (n.id === "approvals" && pending ? { ...n, badge: String(pending) } : n));
  const live = dash?.mandates.filter((m) => m.status === "active").length ?? 0;

  let screen;
  if (error && !dash) screen = <ErrorState message={error} onRetry={refresh} />;
  else if (section === "mandates" && parts[1] && parts[1] !== "new") screen = <MandateScreen id={parts[1]} />;
  else if (section === "mandates") screen = <MandatesScreen />;
  else if (section === "approvals") screen = <ApprovalsScreen />;
  else if (section === "receipts") screen = <ReceiptsScreen />;
  else if (section === "purchases" && parts[1]) screen = <PurchaseScreen id={parts[1]} />;
  else if (section === "activity") screen = <ActivityScreen />;
  else if (section === "redteam") screen = <RedTeamScreen />;
  else if (section === "integration") screen = <IntegrationScreen />;
  else screen = <OverviewScreen />;

  return (
    <>
      <AppShell
        brand={
          <a href="/" aria-label="Leash home" onClick={linkTo("/")}>
            <Wordmark name="LEASH" suffix=".pay" />
          </a>
        }
        nav={[{ items: items.map(({ path: _p, ...n }) => n) }, { section: "Tools", items: TOOLS.map(({ path: _p, ...n }) => n) }]}
        active={active}
        onNavigate={(id) => navigate([...NAV, ...TOOLS].find((n) => n.id === id)!.path)}
        greeting={`Hello, ${dash?.owner ?? status?.owner ?? "there"}`}
        subtitle={
          dash ? (
            dash.frozen ? (
              <span className="text-accent">Kill switch is on. No agent can spend.</span>
            ) : (
              <>
                {live} live mandate{live === 1 ? "" : "s"}
                <span className="hidden sm:inline"> · {money(dash.stats.held)} held by PayPal for your agents</span>
              </>
            )
          ) : (
            "Loading your mandates"
          )
        }
        actions={
          <>
            <div
              onKeyDown={(e) => {
                const v = (e.target as HTMLInputElement).value;
                if (e.key === "Enter" && v.trim()) navigate(`/app/receipts?q=${encodeURIComponent(v.trim())}`);
              }}
            >
              <SearchPill placeholder="Search purchases" />
            </div>
            <IconButton label="Refresh" onClick={refresh}>
              <RefreshCw />
            </IconButton>
            <button
              onClick={() => setConfirmKill(true)}
              aria-label="Kill switch"
              className={cn(
                "grid size-10 place-items-center rounded-full border shadow-[var(--shadow-soft)] transition hover:-translate-y-px [&>svg]:size-[18px] [&>svg]:stroke-[1.7]",
                dash?.frozen ? "border-transparent bg-accent text-white" : "border-hair bg-surface text-accent",
              )}
            >
              <Power />
            </button>
            <div className="hidden sm:block">
              <Button size="sm" onClick={() => navigate("/app/mandates/new")}>
                New mandate
              </Button>
            </div>
          </>
        }
        footer={<ModeFooter />}
      >
        <div className="pb-24 md:pb-0">{screen}</div>
      </AppShell>
      <MobileTabs active={active} pending={pending} />
      <KillSwitchDialog open={confirmKill} onClose={() => setConfirmKill(false)} />
    </>
  );
}

function ModeFooter() {
  const { status } = useLeash();
  if (!status) return null;
  return (
    <div className="space-y-2 px-3 text-[12px]">
      <p className="t-eyebrow !text-[11px]">Running on</p>
      <p className="flex items-center gap-2 text-ink-2">
        <span className={cn("size-2 rounded-full", status.paypal_mode === "sandbox" ? "bg-pos" : "bg-accent")} />
        {status.paypal_mode === "sandbox" ? "PayPal sandbox" : "PayPal fixture (simulated)"}
      </p>
      <p className="flex items-center gap-2 text-ink-2">
        <span className={cn("size-2 rounded-full", status.ai === "rules" ? "bg-muted" : "bg-pos")} />
        {status.ai === "rules" ? "Offline rules (no model key)" : status.ai.replace("nebius:", "").split("/").pop()}
      </p>
    </div>
  );
}

function MobileTabs({ active, pending }: { active: string; pending: number }) {
  return (
    <nav className="glass fixed inset-x-3 bottom-3 z-40 flex justify-around rounded-full px-2 py-1.5 shadow-[var(--shadow-lift)] md:hidden" aria-label="App sections">
      {NAV.map((n) => (
        <a
          key={n.id}
          href={n.path}
          onClick={linkTo(n.path)}
          aria-current={active === n.id ? "page" : undefined}
          className={cn(
            "relative flex flex-col items-center gap-0.5 rounded-full px-3 py-1.5 text-[11px] font-medium [&>svg]:size-[19px] [&>svg]:stroke-[1.6]",
            active === n.id ? "bg-ink text-paper" : "text-ink-2",
          )}
        >
          {n.icon}
          <span className="sr-only sm:not-sr-only">{n.label}</span>
          {n.id === "approvals" && pending > 0 && <span className="absolute right-1 top-0.5 size-2 rounded-full bg-accent" />}
        </a>
      ))}
    </nav>
  );
}

function KillSwitchDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { dash, run, toast } = useLeash();
  const frozen = !!dash?.frozen;
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 grid place-items-center bg-[rgba(26,22,21,0.28)] px-4 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="kill-title"
            className="surface w-full max-w-[440px] p-6"
            initial={{ y: 16, opacity: 0, filter: "blur(8px)" }}
            animate={{ y: 0, opacity: 1, filter: "blur(0px)" }}
            exit={{ y: 8, opacity: 0 }}
            transition={{ duration: 0.4, ease: EASE }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 grid size-11 place-items-center rounded-full bg-accent-soft text-accent">
              <Power size={20} strokeWidth={1.8} />
            </div>
            <h2 id="kill-title" className="font-display text-[20px] font-semibold tracking-[-0.02em]">
              {frozen ? "Release the kill switch?" : "Stop every agent now?"}
            </h2>
            <p className="mt-2 text-[14.5px] leading-relaxed text-ink-2">
              {frozen
                ? "Payments can resume. Mandates revoked by the kill switch stay revoked; create new ones to let agents shop again."
                : "This freezes all payments, revokes every live mandate and voids each PayPal allowance, so held funds are released immediately. Pending approvals are cancelled."}
            </p>
            <div className="mt-6 flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <BusyButton
                label={frozen ? "Release" : "Stop all agents"}
                busyLabel="kill"
                variant={frozen ? "primary" : "accent"}
                onClick={async () => {
                  const res = await run("kill", () => api.killSwitch(!frozen));
                  if (res) toast(frozen ? "Kill switch released." : `Stopped. ${res.revoked?.length ?? 0} mandates revoked and their PayPal allowances voided.`, frozen ? "ok" : "attention");
                  onClose();
                }}
              />
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
