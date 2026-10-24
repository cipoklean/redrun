/* eslint-disable */
/**
 * GENERATED from docs/schemas/link.json by scripts/gen-types.mjs. Do not edit.
 */

export type LinkType = "dependency_match" | "file_match" | "secret_or_config_match" | "config_match";
export type LogEvidenceId = string | null;
export type ChangeEvidenceId = string | null;
export type ScoreHint = number;
export type Detail = string;

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
