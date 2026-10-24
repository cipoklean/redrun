import type { LiveState } from "@/lib/run-state";
import type { RunResult } from "@/types/contract";
import { fmtCount, fmtMs } from "@/lib/format";
import { cn } from "@/lib/utils";

export function StepList({ state }: { state: LiveState }) {
  if (!state.steps.length) {
    return <p className="text-[13px] text-muted">No investigator steps yet.</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      {state.steps.map((step) => (
        <div
          key={step.step}
          className={cn(
            "rounded-md border px-3 py-2",
            step.revision ? "border-warn/50 bg-warn/5" : "border-line bg-raised/40",
          )}
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="mono text-[11px] text-muted">step {step.step}</span>
            <span className="text-[13px] font-medium text-ink">{step.chunk}</span>
            {step.lane && (
              <span className="rounded-sm border border-line px-1 text-[10px] uppercase tracking-wider text-muted">
                {step.lane}
              </span>
            )}
            {step.revision && (
              <span className="rounded-sm border border-warn/50 px-1 text-[10px] uppercase tracking-wider text-warn">
                revision
              </span>
            )}
            <span className="ml-auto mono text-[11px] text-muted">
              {step.tokensIn}/{step.tokensOut} tok
              {step.latencyMs ? `, ${fmtMs(step.latencyMs)}` : ""}
            </span>
          </div>
          {step.model && (
            <p className="mono mt-0.5 truncate text-[11px] text-muted" title={step.model}>
              {step.provider ? `${step.provider}/` : ""}
              {step.model}
            </p>
          )}
          {step.hypotheses.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {step.hypotheses.map((hypothesis, index) => (
                <li key={index} className="text-[12px] text-muted">
                  <span className="mono text-ink/80">{hypothesis.cause}</span>:{" "}
                  {hypothesis.claim || "no claim yet"}
                </li>
              ))}
              {step.openQuestions > 0 && (
                <li className="text-[11px] text-muted">{step.openQuestions} open question(s)</li>
              )}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

export function TokenBar({
  state,
  metrics,
}: {
  state: LiveState;
  metrics?: NonNullable<NonNullable<RunResult["metrics"]>["markovian"]> | null;
}) {
  const full = metrics?.full_context_tokens_estimate ?? state.fullEstimate;
  const used = metrics?.tokens_in ?? state.tokensIn;
  const saved = metrics?.tokens_saved ?? (full !== null ? Math.max(0, full - used) : null);
  const pct = full && full > 0 ? Math.min(100, Math.round((used / full) * 100)) : null;

  return (
    <div className="rounded-md border border-line bg-panel px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <span className="label">tokens sent</span>
        <span className="mono text-[11px] text-muted">
          {fmtCount(used)}
          {full !== null ? ` of ${fmtCount(full)} in one prompt` : " so far"}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line">
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-500"
          style={{ width: `${pct ?? 0}%` }}
          role="progressbar"
          aria-valuenow={pct ?? 0}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="tokens sent against the single prompt estimate"
        />
      </div>
      {saved !== null ? (
        <p className="mt-1 text-[11px] text-muted">
          <span className="text-pass">{fmtCount(saved)} tokens saved</span> by sending chunks instead of
          the whole evidence set
        </p>
      ) : (
        <p className="mt-1 text-[11px] text-muted">
          the one-prompt estimate arrives with the finished result
        </p>
      )}
    </div>
  );
}
