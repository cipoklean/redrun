import { describe, expect, it } from "vitest";

import type { Event as RunEvent } from "@/types/contract";
import { applyEvent, initLiveState, type LiveState } from "@/lib/run-state";

function event(partial: Partial<RunEvent> & { agent: string; type: string; message: string }): RunEvent {
  return { ts: "2026-10-24T10:00:00Z", data: {}, ...partial } as unknown as RunEvent;
}

function fold(events: RunEvent[]): LiveState {
  return events.reduce((state, item) => applyEvent(state, item), initLiveState());
}

describe("live event folding", () => {
  it("lights the stages in order and keeps the engine label", () => {
    const state = fold([
      event({ agent: "triage", type: "start", message: "Investigating sample:x" }),
      event({ agent: "triage", type: "check", message: "triage candidate: bad_commit", data: { cause: "bad_commit" } }),
      event({ agent: "collector", type: "progress", message: "runs: failed #1" }),
      event({ agent: "collector", type: "progress", message: "world: 6 entities, 26 facts" }),
      event({ agent: "zetaris", type: "start", message: "engine=zetaris_replay query=overlap.sql" }),
      event({ agent: "zetaris", type: "result", message: "engine=zetaris_replay rows=1", data: { engine: "zetaris_replay", rows: 1 } }),
    ]);

    expect(state.candidate).toBe("bad_commit");
    expect(state.stages.triage).toBe("done");
    expect(state.stages.collectors).toBe("done");
    expect(state.stages.world).toBe("done");
    expect(state.stages.zetaris).toBe("done");
    expect(state.engine).toBe("zetaris_replay");
    expect(state.zetaris[0]?.rows).toBe(1);
  });

  it("shows a critique and a revision as separate, visible facts", () => {
    const state = fold([
      event({ agent: "investigator", type: "start", message: "step 1 chunk=links", data: { step: 1, chunk: "links" } }),
      event({
        agent: "investigator",
        type: "result",
        message: "step 1 ok",
        data: {
          step: 1,
          chunk: "links",
          carryover: { hypotheses: [{ cause: "bad_commit", claim: "the refactor dropped a guard" }], open_questions: 1 },
        },
      }),
      event({ agent: "investigator", type: "handoff", message: "investigator -> skeptic: verdict proposed" }),
      event({
        agent: "skeptic",
        type: "check",
        message: "skeptic: 3 pass, 2 fail, 1 n/a",
        data: { checks: [{ check: "same_sha_elsewhere", status: "fail", detail: "passed in deploy" }] },
      }),
      event({ agent: "skeptic", type: "handoff", message: "skeptic -> investigator: critique" }),
      event({
        agent: "investigator",
        type: "start",
        message: "step 6 chunk=revision",
        data: { step: 6, chunk: "revision" },
      }),
      event({
        agent: "investigator",
        type: "result",
        message: "flaky_test: rerun the job",
        data: { cause: "flaky_test", confidence: 0.6 },
      }),
    ]);

    expect(state.critiques).toBe(1);
    expect(state.revisions).toBe(1);
    expect(state.stages.skeptic).toBe("warn");
    expect(state.stages.verdict).toBe("done");
    expect(state.status).toBe("done");
    expect(state.steps[0]?.hypotheses[0]?.cause).toBe("bad_commit");
    expect(state.steps[0]?.openQuestions).toBe(1);
    expect(state.checks[0]?.status).toBe("fail");
  });

  it("keeps tokens when progress arrives before the step result", () => {
    const state = fold([
      event({
        agent: "investigator",
        type: "progress",
        message: "step 1 m/fast tokens 407/56",
        data: { step: 1, chunk: "links", lane: "fast", model: "sample/fast", tokens_in: 407, tokens_out: 56 },
      }),
      event({
        agent: "investigator",
        type: "result",
        message: "step 1 ok",
        data: {
          step: 1,
          chunk: "links",
          carryover: { hypotheses: [{ cause: "bad_commit", claim: "dropped guard" }], open_questions: 1 },
        },
      }),
    ]);
    expect(state.steps[0]?.tokensIn).toBe(407);
    expect(state.steps[0]?.tokensOut).toBe(56);
    expect(state.steps[0]?.lane).toBe("fast");
    expect(state.steps[0]?.hypotheses[0]?.cause).toBe("bad_commit");
  });

  it("accumulates tokens only from progress events", () => {
    const state = fold([
      event({
        agent: "investigator",
        type: "progress",
        message: "step 1 m/fast tokens 400/50",
        data: { step: 1, tokens_in: 400, tokens_out: 50 },
      }),
      event({
        agent: "investigator",
        type: "progress",
        message: "step 2 m/fast tokens 600/40",
        data: { step: 2, tokens_in: 600, tokens_out: 40 },
      }),
    ]);
    expect(state.tokensIn).toBe(1000);
    expect(state.tokensOut).toBe(90);
  });

  it("keeps a fallback label rather than hiding it", () => {
    const state = fold([
      event({
        agent: "zetaris",
        type: "result",
        message: "engine=duckdb_fallback rows=3",
        data: { engine: "duckdb_fallback", rows: 3 },
      }),
    ]);
    expect(state.engine).toBe("duckdb_fallback");
    expect(state.stages.zetaris).toBe("warn");
  });
});
