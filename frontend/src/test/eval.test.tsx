import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { EvalPage } from "@/pages/EvalPage";
import type { EvalResults } from "@/lib/api";

const RESULTS: EvalResults = {
  meta: {
    timestamp: "2026-10-25T09:00:00Z",
    mode: "replay",
    split: "all",
    seed: 20261025,
    case_count: 2,
    baseline_model: "nvidia/nemotron-3.5-lightning-30b-a3b",
    baseline_lines: 300,
    git_sha: "abc1234",
    notes: "",
  },
  summaries: {
    dev: {
      agent: summary({ cases: 1, cause_hits: 1, keyword_hits: 1, citation_pass: 1, citation_evaluated: 1 }),
      baseline: summary({ cases: 1, cause_hits: 0, keyword_hits: 0, citation_pass: null, citation_evaluated: 0 }),
    },
    holdout: {
      agent: summary({ cases: 1, cause_hits: 0, keyword_hits: 1 }),
      baseline: summary({ cases: 1, cause_hits: 1, keyword_hits: 1 }),
    },
    all: {
      agent: summary({ cases: 2, cause_hits: 1, keyword_hits: 2, guard_interventions: 1 }),
      baseline: summary({ cases: 2, cause_hits: 1, keyword_hits: 1 }),
    },
  },
  rows: [
    {
      id: "dep_bump_unpinned",
      url: "https://github.com/cipoklean/redrun-lab/actions/runs/1",
      expected_cause: "dependency_bump",
      root_cause_keywords: ["packaging", "pin"],
      split: "dev",
      constructed: true,
      notes: "",
      path: "pipeline",
      path_reason: "",
      diagnostic: {},
      agent: observation({ cause: "dependency_bump", category_ok: true, keywords_ok: true }),
      baseline: observation({ cause: "flaky_test", category_ok: false, keywords_ok: false }),
    },
    {
      id: "infra_outage_a",
      url: "https://github.com/example/repo/actions/runs/2",
      expected_cause: "infra_outage",
      root_cause_keywords: ["runner", "network"],
      split: "holdout",
      constructed: false,
      notes: "",
      path: "rules_pending_llm",
      path_reason: "no recorded LLM step for this case",
      diagnostic: { rules_cause: "unknown" },
      agent: null,
      baseline: null,
    },
  ],
  pending: [
    {
      id: "infra_outage_a",
      split: "holdout",
      path: "rules_pending_llm",
      reason: "no recorded LLM step for this case",
      rules_cause: "unknown",
    },
  ],
  baseline_pending: [{ id: "infra_outage_a", split: "holdout", reason: "not recorded" }],
};

function summary(overrides: Record<string, unknown>) {
  const cases = (overrides.cases as number) ?? 1;
  const causeHits = (overrides.cause_hits as number) ?? 0;
  const keywordHits = (overrides.keyword_hits as number) ?? 0;
  const citationPass = overrides.citation_pass as number | null | undefined;
  const citationEvaluated = (overrides.citation_evaluated as number) ?? 0;
  return {
    system: "agent",
    cases,
    cause_hits: causeHits,
    cause_accuracy: cases ? causeHits / cases : null,
    keyword_hits: keywordHits,
    keyword_accuracy: cases ? keywordHits / cases : null,
    citation_evaluated: citationEvaluated,
    citation_pass: citationPass === undefined ? causeHits : citationPass,
    citation_pass_rate:
      citationEvaluated && citationPass ? citationPass / citationEvaluated : null,
    guard_interventions: (overrides.guard_interventions as number) ?? 0,
    latency_p50_ms: 1200,
    latency_p95_ms: 3400,
    tokens_in: 4200,
    tokens_out: 320,
    calibration: { correct: 0.72, wrong: 0.41, correct_cases: 1, wrong_cases: 1 },
    confusion: {},
  };
}

function observation(overrides: Record<string, unknown>) {
  return {
    cause: null,
    root_cause: "",
    next_step: "",
    confidence: 0.72,
    citation_ok: true,
    category_ok: false,
    keywords_ok: false,
    keywords_missing: [],
    ...overrides,
  } as EvalResults["rows"][number]["agent"];
}

function stubFetch(body: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: status < 400,
      status,
      statusText: status === 404 ? "Not Found" : "OK",
      json: async () => body,
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the eval screen", () => {
  it("shows the totals for dev, holdout and all, agent beside baseline", async () => {
    stubFetch(RESULTS);
    render(<EvalPage />);

    expect(await screen.findByText("Totals")).toBeInTheDocument();
    const splits = screen.getAllByText("dev").length + screen.getAllByText("holdout").length;
    expect(splits).toBeGreaterThanOrEqual(4); // the totals row and the per-case row for each
    // dev: agent 1/1, baseline 0/1
    expect(screen.getAllByText(/1\/1/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/0\/1/).length).toBeGreaterThan(0);
    expect(screen.getByText(/1 guard rejection/)).toBeInTheDocument();
  });

  it("marks a pending case as pending rather than as a wrong answer", async () => {
    stubFetch(RESULTS);
    render(<EvalPage />);

    expect(await screen.findByText(/pending \(rules_pending_llm\)/)).toBeInTheDocument();
    expect(screen.getByText("Pending a live recording")).toBeInTheDocument();
    expect(screen.getByText(/rules-only diagnostic: unknown/)).toBeInTheDocument();
    expect(screen.getByText("Without a recorded baseline")).toBeInTheDocument();
  });

  it("reports the constructed cases as constructed and the public ones as public", async () => {
    stubFetch(RESULTS);
    render(<EvalPage />);

    expect(await screen.findByText("constructed")).toBeInTheDocument();
    expect(screen.getByText("public")).toBeInTheDocument();
    expect(screen.getAllByText("dependency_bump").length).toBeGreaterThan(0);
    expect(screen.getAllByText("flaky_test").length).toBeGreaterThan(0);
  });

  it("says no evaluation has run instead of showing an empty table", async () => {
    stubFetch({ detail: "no evaluation results yet" }, 404);
    render(<EvalPage />);

    expect(await screen.findByText(/No evaluation has been run yet/)).toBeInTheDocument();
    expect(screen.getByText(/python -m redrun eval/)).toBeInTheDocument();
  });
});
