import { ArrowRight, ClipboardCopy, Download, ListTree } from "lucide-react";

import type { RunResult } from "@/types/contract";
import {
  causeLabel,
  causeTone,
  contribution,
  downloadJson,
  engineLabel,
  fmtCount,
  fmtNumber,
  fmtPct,
  statusTone,
  toMarkdown,
} from "@/lib/format";
import { normalizeChecks } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfidenceGauge } from "@/components/ConfidenceGauge";
import { SkepticChecks } from "@/components/SkepticChecks";

const EV = /(ev_[A-Za-z0-9_]+)/g;

function RootCauseText({ text, onCite }: { text: string; onCite: (id: string) => void }) {
  const parts = text.split(EV);
  return (
    <p className="text-[15px] leading-relaxed text-ink">
      {parts.map((part, index) =>
        part.startsWith("ev_") ? (
          <button
            key={`${part}-${index}`}
            type="button"
            className="ev-chip mx-0.5 align-baseline"
            onClick={() => onCite(part)}
            aria-label={`Jump to evidence ${part}`}
          >
            {part}
          </button>
        ) : (
          <span key={`text-${index}`}>{part}</span>
        ),
      )}
    </p>
  );
}

interface Props {
  result: RunResult;
  onCite: (id: string) => void;
  onViewTrace: () => void;
}

export function VerdictView({ result, onCite, onViewTrace }: Props) {
  const verdict = result.verdict;
  if (!verdict) {
    return (
      <div className="rounded-lg border border-fail/40 bg-fail/5 p-4">
        <p className="text-sm text-ink">This run finished without a verdict.</p>
        <p className="mt-1 text-[13px] text-muted">
          The trace is the source of truth: {result.trace_id}
        </p>
      </div>
    );
  }

  const checks = normalizeChecks(result.skeptic?.checks);
  const total = verdict.confidence_breakdown.reduce(
    (sum, signal) => sum + contribution(signal.weight, signal.value),
    0,
  );
  const markovian = result.metrics?.markovian;

  async function copyMarkdown() {
    try {
      await navigator.clipboard.writeText(toMarkdown(result));
    } catch {
      /* clipboard denied, nothing else to do */
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-4 rounded-lg border border-line bg-panel p-4 lg:flex-row lg:items-start">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={causeTone(verdict.cause)} className="text-[12px]">
              {causeLabel(verdict.cause)}
            </Badge>
            <span className="mono text-[11px] text-muted">{verdict.cause}</span>
            {result.skeptic && (
              <Badge tone={statusTone(result.skeptic.final_status ?? "skipped")}>
                skeptic {result.skeptic.final_status}
                {result.skeptic.revisions ? `, ${result.skeptic.revisions} revision` : ""}
              </Badge>
            )}
            <span className="mono text-[11px] text-muted">
              engine {engineLabel(result.metrics?.query_engine)}
            </span>
          </div>

          <h2 className="mt-3 text-[11px] uppercase tracking-[0.14em] text-muted">root cause</h2>
          <RootCauseText text={verdict.root_cause} onCite={onCite} />

          {verdict.cited_evidence_ids.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className="label mr-1">cited</span>
              {verdict.cited_evidence_ids.map((id) => (
                <button
                  key={id}
                  type="button"
                  className="ev-chip"
                  onClick={() => onCite(id)}
                  aria-label={`Jump to evidence ${id}`}
                >
                  {id}
                </button>
              ))}
            </div>
          )}

          <div className="mt-4 rounded-md border border-accent/40 bg-accent-soft/60 p-3">
            <h3 className="label text-accent">next step</h3>
            <p className="mt-1 text-[14px] leading-relaxed text-ink">{verdict.next_step}</p>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button size="sm" variant="secondary" onClick={() => void copyMarkdown()}>
              <ClipboardCopy className="h-3.5 w-3.5" /> Copy as markdown
            </Button>
            <Button size="sm" variant="secondary" onClick={() => downloadJson(result)}>
              <Download className="h-3.5 w-3.5" /> Download JSON
            </Button>
            <Button size="sm" variant="ghost" onClick={onViewTrace}>
              <ListTree className="h-3.5 w-3.5" /> View trace
            </Button>
          </div>
        </div>

        <div className="shrink-0 border-t border-line pt-4 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
          <ConfidenceGauge confidence={verdict.confidence} cause={verdict.cause} />
          <p className="mt-2 max-w-[190px] text-[11px] leading-snug text-muted">
            {fmtPct(verdict.confidence)} confidence. The model never returns a score; this is the
            engine's weighted sum.
          </p>
        </div>
      </div>

      <div className="rounded-lg border border-line bg-panel p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Confidence breakdown</h3>
          <span className="text-[11px] text-muted">computed from signals, not model-reported</span>
        </div>
        <table className="mt-2 w-full border-collapse text-[12px]">
          <thead>
            <tr className="text-left text-muted">
              <th className="py-1 font-medium">signal</th>
              <th className="py-1 text-right font-medium">weight</th>
              <th className="py-1 text-right font-medium">value</th>
              <th className="py-1 text-right font-medium">contribution</th>
            </tr>
          </thead>
          <tbody className="mono">
            {verdict.confidence_breakdown.map((signal) => {
              const part = contribution(signal.weight, signal.value);
              return (
                <tr key={signal.signal} className="border-t border-line/70">
                  <td className="py-1 text-ink">{signal.signal}</td>
                  <td className="py-1 text-right text-muted">{fmtNumber(signal.weight)}</td>
                  <td className="py-1 text-right text-muted">{fmtNumber(signal.value)}</td>
                  <td className={part < 0 ? "py-1 text-right text-fail" : "py-1 text-right text-ink"}>
                    {part >= 0 ? "+" : ""}
                    {fmtNumber(part)}
                  </td>
                </tr>
              );
            })}
            <tr className="border-t border-line">
              <td className="py-1 text-muted" colSpan={3}>
                {verdict.cause === "unknown" && total > 0.5
                  ? "sum, clamped to 0.5 for unknown"
                  : "sum of contributions"}
              </td>
              <td className="py-1 text-right text-ink">{fmtNumber(total)}</td>
            </tr>
            <tr>
              <td className="py-1 text-muted" colSpan={3}>
                reported confidence
              </td>
              <td className="py-1 text-right text-accent">{fmtNumber(verdict.confidence)}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="rounded-lg border border-line bg-panel p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Skeptic checks</h3>
          <span className="text-[11px] text-muted">
            {result.skeptic?.revisions ? "one revision ran" : "no revision needed"}
          </span>
        </div>
        <div className="mt-2">
          <SkepticChecks checks={checks} />
        </div>
        {result.skeptic?.revisions ? (
          <p className="mt-2 flex items-center gap-1 text-[12px] text-warn">
            <ArrowRight className="h-3.5 w-3.5 rotate-180" />
            A decisive check failed, the critique went back, and the investigator revised once.
          </p>
        ) : null}
      </div>

      {markovian && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: "steps", value: fmtCount(markovian.steps) },
            { label: "stop reason", value: markovian.stop_reason ?? "n/a" },
            { label: "tokens in", value: fmtCount(markovian.tokens_in) },
            { label: "tokens saved", value: fmtCount(markovian.tokens_saved) },
          ].map((item) => (
            <div key={item.label} className="rounded-md border border-line bg-panel px-3 py-2">
              <p className="label">{item.label}</p>
              <p className="mono mt-0.5 text-sm text-ink">{item.value}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
