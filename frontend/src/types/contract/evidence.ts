/* eslint-disable */
/**
 * GENERATED from docs/schemas/evidence.json by scripts/gen-types.mjs. Do not edit.
 */

export type Id = string;
export type Kind = "run" | "commit" | "file" | "log" | "dependency" | "outage" | "rerun";
export type SourceUrl = string;
export type Title = string;
export type Excerpt = string;
export type Timestamp = string | null;

/**
 * One citable fact. `excerpt` is always redacted before it is stored.
 */
export interface Evidence {
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
