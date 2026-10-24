/* eslint-disable */
/**
 * GENERATED from docs/schemas/event.json by scripts/gen-types.mjs. Do not edit.
 */

export type Ts = string;
export type Agent = "triage" | "collector" | "zetaris" | "investigator" | "skeptic";
export type Type = "start" | "progress" | "handoff" | "check" | "result" | "error";
export type Message = string;

/**
 * One line of the JSONL trace. Also the SSE payload shape, later.
 */
export interface Event {
  ts?: Ts;
  agent: Agent;
  type: Type;
  message: Message;
  data?: Data;
}
export interface Data {
  [k: string]: unknown;
}
