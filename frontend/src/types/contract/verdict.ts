/* eslint-disable */
/**
 * GENERATED from docs/schemas/verdict.json by scripts/gen-types.mjs. Do not edit.
 */

/**
 * Why a run failed. `unknown` is a real answer, not a placeholder.
 */
export type Cause =
  | "bad_commit"
  | "dependency_bump"
  | "flaky_test"
  | "infra_outage"
  | "expired_or_missing_secret"
  | "config_change"
  | "unknown";
export type RootCause = string;
export type Confidence = number;
export type Signal = string;
export type Weight = number;
export type Value = number;
export type ConfidenceBreakdown = ConfidenceSignal[];
export type NextStep = string;
export type CitedEvidenceIds = string[];

export interface Verdict {
  cause: Cause;
  root_cause: RootCause;
  confidence: Confidence;
  confidence_breakdown: ConfidenceBreakdown;
  next_step: NextStep;
  cited_evidence_ids: CitedEvidenceIds;
}
/**
 * One measured signal behind the confidence number.
 */
export interface ConfidenceSignal {
  signal: Signal;
  weight: Weight;
  value: Value;
}
