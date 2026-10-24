import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Loader2 } from "lucide-react";

import { fetchSamples, type SampleSummary } from "@/lib/api";
import { ProviderStrip } from "@/components/ProviderStrip";
import { SampleCard } from "@/components/SampleCard";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { SettingsDrawer } from "@/components/SettingsDrawer";

export function InvestigatePage() {
  const navigate = useNavigate();
  const [samples, setSamples] = useState<SampleSummary[] | null>(null);
  const [samplesError, setSamplesError] = useState<string | null>(null);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchSamples()
      .then((items) => {
        if (alive) setSamples(items);
      })
      .catch((cause: Error) => {
        if (alive) setSamplesError(cause.message);
      });
    return () => {
      alive = false;
    };
  }, []);

  async function go(body: { target?: string; sample?: string }, label: string) {
    setBusy(true);
    setError(null);
    setActive(label);
    // The run page owns the stream, so this only hands over the choice.
    navigate(`/run/new?${new URLSearchParams(body).toString()}`);
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const value = target.trim();
    if (!value) {
      setError("Paste a run URL or an owner/repo pair.");
      return;
    }
    void go({ target: value }, "target");
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6">
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Why did the run fail?</h1>
            <p className="mt-1 max-w-2xl text-[13px] text-muted">
              A cause category, a cited root cause, a computed confidence score and one next step. Every
              claim points at evidence from the run itself.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setSettingsOpen(true)}>
            Settings
          </Button>
        </div>
        <ProviderStrip onOpenSettings={() => setSettingsOpen(true)} />
      </header>

      <Card className="p-4">
        <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <label className="sr-only" htmlFor="target">
            GitHub Actions run URL or owner/repo
          </label>
          <input
            id="target"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            placeholder="https://github.com/owner/repo/actions/runs/123456789 or owner/repo"
            className="h-10 w-full flex-1 rounded-md border border-line bg-bg px-3 font-mono text-[13px] text-ink placeholder:text-muted/70 focus:border-accent/60 focus:outline-none"
            spellCheck={false}
            autoComplete="off"
          />
          <Button type="submit" variant="primary" size="lg" disabled={busy} aria-label="Investigate run">
            {busy && active === "target" ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <ArrowRight className="h-4 w-4" />
            )}
            Investigate
          </Button>
        </form>
        {error && (
          <p role="alert" className="mt-2 text-[12px] text-fail">
            {error}
          </p>
        )}
        <p className="mt-2 text-[11px] text-muted">
          Live targets need a GitHub token. Samples run offline and replay recorded evidence.
        </p>
      </Card>

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Sample cases</h2>
          <span className="text-[11px] text-muted">no credentials, no network</span>
        </div>

        {samplesError && (
          <div className="rounded-md border border-fail/40 bg-fail/5 p-3 text-[12px] text-fail">
            Could not load samples: {samplesError}
          </div>
        )}

        {!samples && !samplesError && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[0, 1, 2, 3].map((index) => (
              <div key={index} className="h-40 animate-pulse rounded-lg border border-line bg-panel" />
            ))}
          </div>
        )}

        {samples && samples.length === 0 && (
          <p className="text-[13px] text-muted">No samples are installed in this build.</p>
        )}

        {samples && samples.length > 0 && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {samples.map((sample) => (
              <SampleCard
                key={sample.name}
                sample={sample}
                busy={busy}
                active={active === sample.name}
                onRun={(name) => void go({ sample: name }, name)}
              />
            ))}
          </div>
        )}
      </section>

      <SettingsDrawer open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  );
}
