import { AlertTriangle, ArrowUpRight, Check, Loader2 } from "lucide-react";

import { STAGES, type LiveState, type StageStatus } from "@/lib/run-state";
import { cn } from "@/lib/utils";

function StatusMark({ status }: { status: StageStatus }) {
  if (status === "active") {
    return <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" aria-label="active" />;
  }
  if (status === "done") {
    return <Check className="h-3.5 w-3.5 text-pass" aria-label="done" />;
  }
  if (status === "error") {
    return <AlertTriangle className="h-3.5 w-3.5 text-fail" aria-label="error" />;
  }
  if (status === "warn") {
    return <AlertTriangle className="h-3.5 w-3.5 text-warn" aria-label="attention" />;
  }
  return <span className="h-1.5 w-1.5 rounded-full bg-line" aria-hidden />;
}

export function Pipeline({ state }: { state: LiveState }) {
  return (
    <ol className="relative flex flex-col gap-0" aria-label="Investigation pipeline">
      {STAGES.map((stage, index) => {
        const status = state.stages[stage.id];
        const last = index === STAGES.length - 1;
        return (
          <li key={stage.id} className="relative flex gap-3 pb-3 last:pb-0">
            {!last && (
              <span
                className={cn(
                  "absolute left-[9px] top-6 h-[calc(100%-12px)] w-px",
                  status === "done" ? "bg-pass/40" : "bg-line",
                )}
                aria-hidden
              />
            )}
            <span
              className={cn(
                "z-10 mt-1 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border",
                status === "active" && "border-accent/70 bg-accent/10",
                status === "done" && "border-pass/50 bg-pass/10",
                status === "warn" && "border-warn/60 bg-warn/10",
                status === "error" && "border-fail/60 bg-fail/10",
                status === "idle" && "border-line bg-panel",
              )}
            >
              <StatusMark status={status} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    "text-[13px] font-medium",
                    status === "idle" ? "text-muted" : "text-ink",
                  )}
                >
                  {stage.label}
                </span>
                {stage.id === "triage" && state.candidate && (
                  <span className="mono text-[11px] text-muted">{state.candidate}</span>
                )}
                {stage.id === "investigator" && state.steps.length > 0 && (
                  <span className="mono text-[11px] text-muted">
                    {state.steps.length} step{state.steps.length === 1 ? "" : "s"}
                  </span>
                )}
                {stage.id === "skeptic" && state.checks.length > 0 && (
                  <span className="mono text-[11px] text-muted">
                    {state.checks.filter((item) => item.status === "pass").length} pass
                    {state.checks.some((item) => item.status === "fail") ? " / fail" : ""}
                  </span>
                )}
              </div>
              <p className="text-[12px] text-muted">{stage.hint}</p>

              {stage.id === "skeptic" && state.critiques > 0 && (
                <div className="mt-1 inline-flex items-center gap-1 rounded-sm border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[11px] text-warn">
                  <ArrowUpRight className="h-3 w-3" />
                  critique sent back to the investigator, revision {state.revisions}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
