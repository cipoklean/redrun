import type { RunResult } from "@/types/contract";

export const CAUSE_LABELS: Record<string, string> = {
  bad_commit: "Bad commit",
  dependency_bump: "Dependency bump",
  flaky_test: "Flaky test",
  infra_outage: "Infra outage",
  expired_or_missing_secret: "Missing secret",
  config_change: "Config change",
  unknown: "Unknown",
};

export function causeLabel(cause: string): string {
  return CAUSE_LABELS[cause] ?? cause.replace(/_/g, " ");
}

export type Tone = "accent" | "pass" | "fail" | "warn" | "muted";

export function causeTone(cause: string): Tone {
  switch (cause) {
    case "unknown":
      return "muted";
    case "infra_outage":
    case "flaky_test":
      return "warn";
    case "expired_or_missing_secret":
    case "config_change":
      return "accent";
    default:
      return "accent";
  }
}

export function statusTone(status: string): Tone {
  switch (status) {
    case "online":
    case "pass":
    case "passed":
      return "pass";
    case "unauthorized":
    case "fail":
    case "failed":
      return "fail";
    case "offline":
    case "n/a":
    case "sample":
      return "muted";
    case "revised":
      return "warn";
    default:
      return "muted";
  }
}

export function fmtTime(value?: string | null): string {
  if (!value) return "unknown time";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function fmtShortTime(value?: string | null): string {
  if (!value) return "?";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function fmtMs(ms?: number | null): string {
  if (ms === null || ms === undefined) return "n/a";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function fmtCount(value?: number | null): string {
  if (value === null || value === undefined) return "0";
  return value.toLocaleString();
}

export function fmtPct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function fmtNumber(value: number, digits = 3): string {
  return value.toFixed(digits);
}

/** The engine label is always shown, including replay and fallback. */
export function engineLabel(engine?: string | null): string {
  switch (engine) {
    case "zetaris":
      return "Zetaris";
    case "zetaris_replay":
      return "Zetaris replay";
    case "duckdb":
      return "DuckDB";
    case "duckdb_fallback":
      return "DuckDB fallback";
    default:
      return engine ?? "unknown engine";
  }
}

export function engineNote(engine?: string | null): string {
  switch (engine) {
    case "zetaris_replay":
      return "recorded result replayed, no live query ran";
    case "duckdb_fallback":
      return "the configured engine failed, local DuckDB answered";
    case "duckdb":
      return "local DuckDB over the evidence tables";
    default:
      return "";
  }
}

export function contribution(weight: number, value: number): number {
  return weight * value;
}

export function clampConfidence(total: number, cause: string): number {
  const cap = cause === "unknown" ? 0.5 : 1;
  return Math.max(0, Math.min(cap, total));
}

export function toMarkdown(result: RunResult): string {
  const verdict = result.verdict;
  const lines: string[] = [];
  lines.push(`# RedRun verdict: ${causeLabel(verdict?.cause ?? "unknown")}`);
  lines.push("");
  lines.push(`Run: ${result.trace_id}  `);
  lines.push(`Cause: \`${verdict?.cause ?? "unknown"}\`  `);
  if (verdict) {
    lines.push(`Confidence: ${fmtPct(verdict.confidence)} (computed from signals, not model-reported)  `);
  }
  if (result.skeptic) {
    lines.push(`Skeptic: ${result.skeptic.final_status} after ${result.skeptic.revisions} revision(s)  `);
  }
  lines.push("");
  lines.push("## Root cause");
  lines.push("");
  lines.push(verdict?.root_cause ?? "no verdict");
  lines.push("");
  lines.push("## Next step");
  lines.push("");
  lines.push(verdict?.next_step ?? "n/a");
  lines.push("");
  if (verdict?.cited_evidence_ids.length) {
    lines.push("## Cited evidence");
    lines.push("");
    const evidence = result.evidence ?? [];
    for (const id of verdict.cited_evidence_ids) {
      const item = evidence.find((entry) => entry.id === id);
      lines.push(`- \`${id}\` ${item ? item.title : "(missing)"}`);
      if (item) lines.push(`  ${item.source_url}`);
    }
    lines.push("");
  }
  if (verdict?.confidence_breakdown.length) {
    lines.push("## Confidence breakdown");
    lines.push("");
    lines.push("| signal | weight | value | contribution |");
    lines.push("| --- | --- | --- | --- |");
    for (const signal of verdict.confidence_breakdown) {
      const part = contribution(signal.weight, signal.value);
      lines.push(
        `| ${signal.signal} | ${signal.weight} | ${fmtNumber(signal.value)} | ${fmtNumber(part)} |`,
      );
    }
    lines.push("");
  }
  if (result.skeptic) {
    lines.push("## Skeptic checks");
    lines.push("");
    for (const check of result.skeptic.checks ?? []) {
      lines.push(`- ${(check.status ?? "n/a").toUpperCase()} \`${check.check}\`: ${check.detail}`);
    }
    lines.push("");
  }
  lines.push(`Trace: logs/${result.trace_id}.jsonl`);
  return lines.join("\n");
}

export function downloadJson(result: RunResult): void {
  const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `redrun-${result.trace_id}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}
