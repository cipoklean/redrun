/* eslint-disable */
/**
 * GENERATED from docs/schemas/run_result.json by scripts/gen-types.mjs. Do not edit.
 */

export type RunId = string;
export type Input = string;
export type RunId1 = number;
export type WorkflowName = string;
export type WorkflowPath = string;
export type Branch = string;
export type HeadSha = string;
export type CreatedAt = string;
export type Conclusion = string;
export type RunnerImage = string | null;
export type JobId = number;
export type Name = string;
export type Conclusion1 = string;
export type Number = number;
export type Name1 = string;
export type Conclusion2 = string;
export type Steps = StepInfo[];
export type Jobs = JobInfo[];
export type Id = string;
export type Kind = "run" | "commit" | "file" | "log" | "dependency" | "outage" | "rerun";
export type SourceUrl = string;
export type Title = string;
export type Excerpt = string;
export type Timestamp = string | null;
export type Evidence = Evidence1[];
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
export type LinkType = "dependency_match" | "file_match" | "secret_or_config_match" | "config_match";
export type LogEvidenceId = string | null;
export type ChangeEvidenceId = string | null;
export type ScoreHint = number;
export type Detail = string;
export type Links = Link[];
export type Check = string;
export type Status = "pass" | "fail" | "n/a";
export type Detail1 = string;
export type EvidenceIds = string[];
export type Checks = CheckResult[];
export type Revisions = number;
export type FinalStatus = "passed" | "revised" | "failed" | "skipped";
export type TraceId = string;
export type LlmCalls = number;
export type TokensIn = number;
export type TokensOut = number;
export type LatencyMs = number;
export type QueryEngine = string | null;
export type Steps1 = number;
export type StopReason = "rule" | "cap" | "error";
export type TokensIn1 = number;
export type TokensOut1 = number;
export type CarryoverTokensMax = number;
export type FullContextTokensEstimate = number;
export type TokensSaved = number;
export type Step = number;
export type Chunk = string;
export type Lane = string;
export type Provider = string;
export type Model = string;
export type TokensIn2 = number;
export type TokensOut2 = number;
export type LatencyMs1 = number;
export type Ok = boolean;
export type PerStep = StepMetrics[];

export interface RunResult {
  run_id: RunId;
  input: Input;
  failed_run: RunInfo;
  last_green_run?: RunInfo | null;
  evidence?: Evidence;
  verdict?: Verdict | null;
  links?: Links;
  skeptic?: SkepticReport | null;
  trace_id: TraceId;
  metrics?: Metrics;
}
export interface RunInfo {
  run_id: RunId1;
  workflow_name: WorkflowName;
  workflow_path: WorkflowPath;
  branch: Branch;
  head_sha: HeadSha;
  created_at: CreatedAt;
  conclusion: Conclusion;
  runner_image?: RunnerImage;
  jobs?: Jobs;
}
export interface JobInfo {
  job_id: JobId;
  name: Name;
  conclusion: Conclusion1;
  steps?: Steps;
}
export interface StepInfo {
  number: Number;
  name: Name1;
  conclusion: Conclusion2;
}
/**
 * One citable fact. `excerpt` is always redacted before it is stored.
 */
export interface Evidence1 {
  id: Id;
  kind: Kind;
  source_url: SourceUrl;
  title: Title;
  excerpt: Excerpt;
  timestamp?: Timestamp;
  metadata?: Metadata;
}
export interface Metadata {
  [k: string]: unknown;
}
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
/**
 * One cross-source join from overlap.sql: a log error tied to a change.
 */
export interface Link {
  link_type: LinkType;
  log_evidence_id?: LogEvidenceId;
  change_evidence_id?: ChangeEvidenceId;
  score_hint: ScoreHint;
  detail: Detail;
}
export interface SkepticReport {
  checks?: Checks;
  revisions?: Revisions;
  final_status?: FinalStatus;
}
/**
 * One skeptic tool check. `status` says whether it supports the proposal.
 */
export interface CheckResult {
  check: Check;
  status: Status;
  detail: Detail1;
  evidence_ids?: EvidenceIds;
}
export interface Metrics {
  llm_calls?: LlmCalls;
  tokens_in?: TokensIn;
  tokens_out?: TokensOut;
  latency_ms?: LatencyMs;
  query_engine?: QueryEngine;
  markovian?: MarkovianMetrics | null;
}
export interface MarkovianMetrics {
  steps?: Steps1;
  stop_reason?: StopReason;
  tokens_in?: TokensIn1;
  tokens_out?: TokensOut1;
  carryover_tokens_max?: CarryoverTokensMax;
  full_context_tokens_estimate?: FullContextTokensEstimate;
  tokens_saved?: TokensSaved;
  per_step?: PerStep;
}
/**
 * One LLM step of the Markovian investigator.
 */
export interface StepMetrics {
  step: Step;
  chunk: Chunk;
  lane: Lane;
  provider: Provider;
  model: Model;
  tokens_in: TokensIn2;
  tokens_out: TokensOut2;
  latency_ms: LatencyMs1;
  ok?: Ok;
}
