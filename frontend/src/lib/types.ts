import type { RunResult } from "@/types/contract";

export type SkepticReport = NonNullable<RunResult["skeptic"]>;
export type CheckResult = NonNullable<SkepticReport["checks"]>[number];
export type EvidenceItem = NonNullable<RunResult["evidence"]>[number];
export type VerdictModel = NonNullable<RunResult["verdict"]>;
export type ConfidenceSignal = NonNullable<VerdictModel["confidence_breakdown"]>[number];
export type RunMetrics = NonNullable<RunResult["metrics"]>;
export type StepMetrics = NonNullable<NonNullable<RunMetrics["markovian"]>["per_step"]>[number];

export interface NormalizedCheck {
  check: string;
  status: "pass" | "fail" | "n/a";
  detail: string;
  evidence_ids?: string[];
}

export function normalizeChecks(checks: CheckResult[] | undefined): NormalizedCheck[] {
  return (checks ?? []).map((item) => ({
    check: item.check ?? "unknown",
    status: item.status === "pass" || item.status === "fail" ? item.status : "n/a",
    detail: item.detail ?? "",
    evidence_ids: item.evidence_ids ?? [],
  }));
}

export const EVIDENCE_KINDS = [
  "log",
  "commit",
  "file",
  "dependency",
  "outage",
  "run",
  "rerun",
] as const;

export const EVIDENCE_LABELS: Record<string, string> = {
  log: "Logs",
  commit: "Commits",
  file: "Files",
  dependency: "Dependencies",
  outage: "Outages",
  run: "Runs",
  rerun: "Re-runs",
};
