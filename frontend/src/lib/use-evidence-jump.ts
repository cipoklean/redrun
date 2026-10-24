import { useCallback, useState } from "react";

/** Highlights one evidence card and scrolls it into view, then clears. */
export function useEvidenceJump(resetMs = 1600) {
  const [highlight, setHighlight] = useState<string | null>(null);

  const jump = useCallback(
    (evidenceId: string) => {
      setHighlight(evidenceId);
      const element = document.querySelector(`[data-evidence-id="${evidenceId}"]`);
      element?.scrollIntoView({ behavior: "smooth", block: "center" });
      window.setTimeout(() => setHighlight(null), resetMs);
    },
    [resetMs],
  );

  return { highlight, jump };
}
