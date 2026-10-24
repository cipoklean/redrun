import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";

import { fetchProviderStatus, testConnection, type ProviderStatus, type ProviderTest } from "@/lib/api";
import { fmtMs } from "@/lib/format";
import { Dialog, DialogDescription, DialogTitle, DrawerContent } from "@/components/ui/drawer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-line/70 py-1.5 last:border-0">
      <span className="text-[12px] text-muted">{label}</span>
      <span className="mono max-w-[62%] break-all text-right text-[12px] text-ink">{value}</span>
    </div>
  );
}

export function SettingsDrawer({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [test, setTest] = useState<ProviderTest | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await fetchProviderStatus());
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  async function runTest() {
    setBusy(true);
    setError(null);
    try {
      setTest(await testConnection());
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DrawerContent aria-describedby="settings-description">
        <div className="border-b border-line px-5 py-4">
          <DialogTitle className="text-sm font-semibold">Settings</DialogTitle>
          <DialogDescription id="settings-description" className="mt-1 text-[12px] text-muted">
            What this instance is configured to use. Token values are never shown, only whether one
            is set.
          </DialogDescription>
        </div>

        <div className="flex-1 overflow-auto px-5 py-4">
          {error && (
            <div className="mb-3 rounded-md border border-fail/40 bg-fail/5 px-3 py-2 text-[12px] text-fail">
              {error}
            </div>
          )}

          {!status && !error && <p className="text-[12px] text-muted">Loading configuration...</p>}

          {status && (
            <div className="flex flex-col gap-5">
              <section>
                <h3 className="label mb-1">Model provider</h3>
                <Row label="provider" value={status.llm.provider} />
                <Row label="status" value={<Badge tone={status.llm.status === "online" ? "pass" : status.llm.status === "unauthorized" ? "fail" : "muted"}>{status.llm.status}</Badge>} />
                <Row label="base url" value={status.llm.base_url ?? "not applicable in sample mode"} />
                <Row label="fast lane" value={status.llm.models.fast} />
                <Row label="deep lane" value={status.llm.models.deep} />
                <Row label="judge lane" value={status.llm.models.judge} />
                <Row label="runtime token" value={status.llm.token_set ? "set" : "not set"} />
              </section>

              <section>
                <h3 className="label mb-1">Zetaris</h3>
                <Row label="status" value={status.zetaris.status} />
                <Row label="enabled" value={status.zetaris.enabled ? "yes" : "no"} />
                <Row label="configured" value={status.zetaris.configured ? "yes" : "no"} />
                <Row label="url" value={status.zetaris.url ?? "not set"} />
              </section>

              <section>
                <h3 className="label mb-1">Sources</h3>
                <Row label="GitHub token" value={status.github.token_set ? "set" : "not set"} />
                <Row label="sample mode" value={status.sample_mode ? "on, replays recordings" : "off, live calls"} />
                <Row label="samples" value={status.samples} />
              </section>
            </div>
          )}

          <div className="mt-5 rounded-md border border-line bg-raised/40 p-3">
            <div className="flex items-center justify-between">
              <h3 className="text-[13px] font-medium">Test connection</h3>
              <Button size="sm" variant="primary" onClick={() => void runTest()} disabled={busy}>
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                {busy ? "Testing" : "Run test"}
              </Button>
            </div>
            <p className="mt-1 text-[11px] text-muted">
              Calls the model runtime and Zetaris. Reports status, model count and latency only.
            </p>

            {test && (
              <div className="mt-3 flex flex-col gap-1">
                <p className="flex items-center gap-1.5 text-[12px] text-ink">
                  {test.llm.status === "online" || test.llm.status === "sample" ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-pass" />
                  ) : (
                    <XCircle className="h-3.5 w-3.5 text-fail" />
                  )}
                  {test.summary}
                </p>
                <Row label="provider" value={test.llm.provider} />
                <Row label="models listed" value={test.llm.model_count} />
                <Row label="latency" value={fmtMs(test.llm.latency_ms)} />
                <Row label="zetaris" value={`${test.zetaris.status}, ${fmtMs(test.zetaris.latency_ms)}`} />
              </div>
            )}
          </div>
        </div>
      </DrawerContent>
    </Dialog>
  );
}
