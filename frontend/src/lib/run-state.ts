import type { Event as RunEvent } from "@/types/contract";

export type StageId =
  | "triage"
  | "collectors"
  | "zetaris"
  | "world"
  | "investigator"
  | "skeptic"
  | "verdict";

export type StageStatus = "idle" | "active" | "done" | "warn" | "error";

export interface StageDef {
  id: StageId;
  label: string;
  hint: string;
}

export const STAGES: StageDef[] = [
  { id: "triage", label: "Triage", hint: "pick a candidate category" },
  { id: "collectors", label: "Collectors", hint: "runs, diff, logs, dependencies" },
  { id: "zetaris", label: "Zetaris", hint: "one cross-source query" },
  { id: "world", label: "World Model", hint: "entities and facts" },
  { id: "investigator", label: "Investigator", hint: "Markovian steps" },
  { id: "skeptic", label: "Skeptic", hint: "six tool checks" },
  { id: "verdict", label: "Verdict", hint: "cited and scored" },
];

export interface StepView {
  step: number;
  chunk: string;
  lane?: string;
  model?: string;
  provider?: string;
  tokensIn: number;
  tokensOut: number;
  latencyMs: number;
  ok: boolean;
  revision: boolean;
  hypotheses: { cause: string; claim: string }[];
  openQuestions: number;
}

export interface CheckView {
  check: string;
  status: "pass" | "fail" | "n/a";
  detail: string;
}

export interface ZetarisView {
  engine: string;
  rows: number | null;
  latencyMs: number | null;
  detail: string;
}

export interface LiveState {
  status: "idle" | "running" | "done" | "error";
  error: string | null;
  candidate: string | null;
  stages: Record<StageId, StageStatus>;
  steps: StepView[];
  checks: CheckView[];
  critiques: number;
  revisions: number;
  zetaris: ZetarisView[];
  events: RunEvent[];
  tokensIn: number;
  tokensOut: number;
  engine: string | null;
  summary: string | null;
  tokensSaved: number | null;
  fullEstimate: number | null;
}

export function initLiveState(): LiveState {
  return {
    status: "idle",
    error: null,
    candidate: null,
    stages: {
      triage: "idle",
      collectors: "idle",
      zetaris: "idle",
      world: "idle",
      investigator: "idle",
      skeptic: "idle",
      verdict: "idle",
    },
    steps: [],
    checks: [],
    critiques: 0,
    revisions: 0,
    zetaris: [],
    events: [],
    tokensIn: 0,
    tokensOut: 0,
    engine: null,
    summary: null,
    tokensSaved: null,
    fullEstimate: null,
  };
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function withStage(
  stages: Record<StageId, StageStatus>,
  id: StageId,
  status: StageStatus,
): Record<StageId, StageStatus> {
  return { ...stages, [id]: status };
}

/** Fold one trace event into the live view. Pure, so it can be tested alone. */
export function applyEvent(state: LiveState, event: RunEvent): LiveState {
  const data = (event.data ?? {}) as Record<string, unknown>;
  const next: LiveState = {
    ...state,
    events: [...state.events, event],
    stages: { ...state.stages },
  };

  if (event.agent === "triage") {
    if (event.type === "start") {
      next.status = "running";
      next.stages = withStage(next.stages, "triage", "active");
    }
    if (event.type === "check") {
      const cause = str(data.cause) ?? event.message.split(":").pop()?.trim() ?? null;
      next.candidate = cause;
      next.stages = withStage(next.stages, "triage", "done");
    }
    if (event.type === "error") next.stages = withStage(next.stages, "triage", "error");
  }

  if (event.agent === "collector") {
    if (event.message.startsWith("world:")) {
      next.stages = withStage(next.stages, "world", "done");
      next.stages = withStage(next.stages, "collectors", "done");
    } else {
      next.stages = withStage(next.stages, "collectors", "active");
    }
    if (event.type === "error") next.stages = withStage(next.stages, "collectors", "error");
  }

  if (event.agent === "zetaris") {
    const engine = str(data.engine) ?? /engine=([a-z_]+)/.exec(event.message)?.[1] ?? null;
    if (engine) next.engine = engine;
    if (event.type === "start") next.stages = withStage(next.stages, "zetaris", "active");
    if (event.type === "result") {
      next.stages = withStage(next.stages, "zetaris", engine?.includes("fallback") ? "warn" : "done");
      next.zetaris = [
        ...next.zetaris,
        {
          engine: engine ?? "unknown",
          rows: num(data.rows),
          latencyMs: num(data.latency_ms),
          detail: event.message,
        },
      ];
    }
    if (event.type === "error") {
      next.stages = withStage(next.stages, "zetaris", "error");
      next.zetaris = [
        ...next.zetaris,
        { engine: engine ?? "unknown", rows: null, latencyMs: null, detail: event.message },
      ];
    }
  }

  if (event.agent === "investigator") {
    if (event.type === "start") {
      next.stages = withStage(next.stages, "investigator", "active");
      const chunk = str(data.chunk) ?? "";
      if (chunk === "revision") next.revisions = Math.max(next.revisions, 1);
    }
    if (event.type === "progress") {
      next.tokensIn = state.tokensIn + (num(data.tokens_in) ?? 0);
      next.tokensOut = state.tokensOut + (num(data.tokens_out) ?? 0);
      const step = num(data.step);
      if (step !== null) {
        // Progress can arrive before the step's result event, so upsert rather than update.
        const existing = next.steps.find((item) => item.step === step);
        const chunk = str(data.chunk) ?? existing?.chunk ?? "step";
        const view: StepView = {
          step,
          chunk,
          revision: chunk === "revision" || (existing?.revision ?? false),
          lane: str(data.lane) ?? existing?.lane ?? undefined,
          model: str(data.model) ?? existing?.model ?? undefined,
          provider: str(data.provider) ?? existing?.provider ?? undefined,
          tokensIn: num(data.tokens_in) ?? existing?.tokensIn ?? 0,
          tokensOut: num(data.tokens_out) ?? existing?.tokensOut ?? 0,
          latencyMs: num(data.latency_ms) ?? existing?.latencyMs ?? 0,
          ok: existing?.ok ?? true,
          hypotheses: existing?.hypotheses ?? [],
          openQuestions: existing?.openQuestions ?? 0,
        };
        next.steps = [...next.steps.filter((item) => item.step !== step), view].sort(
          (a, b) => a.step - b.step,
        );
      }
    }
    if (event.type === "result" && data.cause) {
      next.summary = `${String(data.cause)}: ${event.message.split(": ").slice(1).join(": ")}`;
      next.stages = withStage(next.stages, "verdict", "done");
      next.status = "done";
    } else if (event.type === "result") {
      const step = num(data.step);
      const carry = (data.carryover ?? {}) as {
        hypotheses?: { cause?: string; claim?: string }[];
        open_questions?: number;
      };
      if (step !== null) {
        const existing = next.steps.find((item) => item.step === step);
        const view: StepView = {
          step,
          chunk: str(data.chunk) ?? existing?.chunk ?? "step",
          revision: (str(data.chunk) ?? existing?.chunk) === "revision",
          lane: existing?.lane,
          model: existing?.model,
          provider: existing?.provider,
          // Keep the token counts the progress event already carried.
          tokensIn: existing?.tokensIn ?? 0,
          tokensOut: existing?.tokensOut ?? 0,
          latencyMs: existing?.latencyMs ?? 0,
          ok: true,
          hypotheses: (carry.hypotheses ?? []).map((item) => ({
            cause: item.cause ?? "unknown",
            claim: item.claim ?? "",
          })),
          openQuestions: carry.open_questions ?? 0,
        };
        next.steps = [...next.steps.filter((item) => item.step !== step), view].sort(
          (a, b) => a.step - b.step,
        );
      }
    }
    if (event.type === "handoff") {
      next.stages = withStage(next.stages, "investigator", "done");
      next.stages = withStage(next.stages, "skeptic", "active");
    }
    if (event.type === "error") next.stages = withStage(next.stages, "investigator", "error");
  }

  if (event.agent === "skeptic") {
    if (event.type === "check") {
      const checks = Array.isArray(data.checks) ? (data.checks as unknown[]) : [];
      if (checks.length) {
        next.checks = checks.map((raw) => {
          const item = raw as { check?: string; status?: string; detail?: string };
          const status = item.status === "pass" || item.status === "fail" ? item.status : "n/a";
          return { check: item.check ?? "unknown", status, detail: item.detail ?? "" };
        });
        const failed = next.checks.some((item) => item.status === "fail");
        next.stages = withStage(next.stages, "skeptic", failed ? "warn" : "done");
        if (failed) next.stages = withStage(next.stages, "verdict", "active");
      } else if (typeof data.decisive_failed !== "undefined") {
        next.stages = withStage(next.stages, "skeptic", "warn");
      }
    }
    if (event.type === "handoff") {
      next.critiques = state.critiques + 1;
      next.stages = withStage(next.stages, "skeptic", "warn");
      next.stages = withStage(next.stages, "investigator", "active");
      next.stages = withStage(next.stages, "verdict", "idle");
    }
    if (event.type === "error") next.stages = withStage(next.stages, "skeptic", "error");
  }

  return next;
}

export function applyEvents(state: LiveState, events: RunEvent[]): LiveState {
  return events.reduce(applyEvent, state);
}

/** Tokens a single all-evidence prompt would have cost, when the run reports it. */
export function tokenBar(state: LiveState): {
  used: number;
  full: number | null;
  saved: number | null;
} {
  const full = state.fullEstimate ?? null;
  const saved = full !== null ? Math.max(0, full - state.tokensIn) : null;
  return { used: state.tokensIn, full, saved };
}
