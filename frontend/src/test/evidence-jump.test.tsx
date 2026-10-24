import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { RunResult } from "@/types/contract";
import { VerdictView } from "@/components/VerdictView";
import { EvidenceList } from "@/components/EvidenceList";
import { useEvidenceJump } from "@/lib/use-evidence-jump";
import flakyTest from "@/test/fixtures/flaky_test.json";

const result = flakyTest as unknown as RunResult;

function Harness() {
  const { highlight, jump } = useEvidenceJump(50);
  return (
    <div>
      <VerdictView result={result} onCite={jump} onViewTrace={() => {}} />
      <EvidenceList
        evidence={result.evidence ?? []}
        cited={result.verdict?.cited_evidence_ids ?? []}
        highlight={highlight}
      />
    </div>
  );
}

describe("evidence chips", () => {
  it("scrolls to and highlights the matching card", () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    render(<Harness />);

    const cited = result.verdict?.cited_evidence_ids ?? [];
    const target = cited[0];
    expect(target).toBeTruthy();
    if (!target) return;

    const card = document.querySelector(`[data-evidence-id="${target}"]`);
    expect(card).not.toBeNull();
    expect(card?.className).not.toContain("card-flash");

    const chip = screen.getAllByRole("button", { name: `Jump to evidence ${target}` })[0];
    expect(chip).toBeTruthy();
    if (!chip) return;
    fireEvent.click(chip);

    expect(scroll).toHaveBeenCalled();
    const highlighted = document.querySelector(`[data-evidence-id="${target}"]`);
    expect(highlighted?.className).toContain("card-flash");
  });

  it("marks cited cards and leaves the rest plain", () => {
    render(<Harness />);
    const cited = new Set(result.verdict?.cited_evidence_ids ?? []);
    const cards = Array.from(document.querySelectorAll("[data-evidence-id]"));
    expect(cards.length).toBeGreaterThan(0);
    const citedCards = cards.filter((card) => cited.has(card.getAttribute("data-evidence-id") ?? ""));
    expect(citedCards.length).toBe(cited.size);
    for (const card of citedCards) {
      expect(card.textContent).toContain("cited");
    }
  });
});
