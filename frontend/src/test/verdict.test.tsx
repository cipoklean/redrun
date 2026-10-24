import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import type { RunResult } from "@/types/contract";
import { VerdictView } from "@/components/VerdictView";
import { causeLabel, contribution } from "@/lib/format";
import badCommit from "@/test/fixtures/bad_commit.json";
import dependencyBump from "@/test/fixtures/dependency_bump.json";
import expiredSecret from "@/test/fixtures/expired_or_missing_secret.json";
import flakyTest from "@/test/fixtures/flaky_test.json";

const CASES: { name: string; result: RunResult }[] = [
  { name: "dependency_bump", result: dependencyBump as unknown as RunResult },
  { name: "bad_commit", result: badCommit as unknown as RunResult },
  { name: "expired_or_missing_secret", result: expiredSecret as unknown as RunResult },
  { name: "flaky_test", result: flakyTest as unknown as RunResult },
];

describe("verdict rendering from a recorded RunResult", () => {
  for (const item of CASES) {
    it(`renders ${item.name} with its cause, root cause and next step`, () => {
      const verdict = item.result.verdict;
      expect(verdict).toBeTruthy();
      if (!verdict) return;

      render(<VerdictView result={item.result} onCite={() => {}} onViewTrace={() => {}} />);

      // The badge carries the human label, the mono line carries the raw cause.
      expect(screen.getAllByText(causeLabel(verdict.cause)).length).toBeGreaterThan(0);
      expect(screen.getAllByText(verdict.cause).length).toBeGreaterThan(0);
      expect(screen.getByText(verdict.next_step)).toBeInTheDocument();

      const gauge = screen.getByRole("img", { name: /computed confidence/i });
      expect(gauge).toBeInTheDocument();
      expect(gauge.getAttribute("aria-label")).toContain(
        (verdict.confidence * 100).toFixed(1),
      );

      // Every signal is listed with its contribution.
      for (const signal of verdict.confidence_breakdown) {
        expect(screen.getAllByText(signal.signal).length).toBeGreaterThan(0);
      }

      // The skeptic panel is present with one row per check.
      expect(screen.getByText("Skeptic checks")).toBeInTheDocument();
      for (const check of item.result.skeptic?.checks ?? []) {
        expect(screen.getAllByText(check.check ?? "").length).toBeGreaterThan(0);
      }
    });

    it(`labels ${item.name} as computed, not model reported`, () => {
      render(<VerdictView result={item.result} onCite={() => {}} onViewTrace={() => {}} />);
      expect(
        screen.getAllByText(/computed from signals, not model-reported/i).length,
      ).toBeGreaterThan(0);
    });

    it(`keeps the breakdown sum in line with the reported ${item.name} confidence`, () => {
      const verdict = item.result.verdict;
      if (!verdict) return;
      const total = verdict.confidence_breakdown.reduce(
        (sum, signal) => sum + contribution(signal.weight, signal.value),
        0,
      );
      const cap = verdict.cause === "unknown" ? 0.5 : 1;
      const clamped = Math.max(0, Math.min(cap, total));
      expect(clamped).toBeCloseTo(verdict.confidence, 3);
    });
  }

  it("shows a fallback when the run produced no verdict", () => {
    const empty: RunResult = {
      ...(CASES[0]?.result as RunResult),
      verdict: null,
    };
    render(<VerdictView result={empty} onCite={() => {}} onViewTrace={() => {}} />);
    expect(screen.getByText(/without a verdict/i)).toBeInTheDocument();
  });

  it("renders cited ids as chips inside the breakdown card", () => {
    const result = CASES[1]?.result as RunResult;
    render(<VerdictView result={result} onCite={() => {}} onViewTrace={() => {}} />);
    const cited = result.verdict?.cited_evidence_ids ?? [];
    expect(cited.length).toBeGreaterThan(0);
    const row = screen.getByText("cited").parentElement;
    expect(row).not.toBeNull();
    if (!row) return;
    for (const id of cited) {
      expect(within(row).getByText(id)).toBeInTheDocument();
    }
  });
});
