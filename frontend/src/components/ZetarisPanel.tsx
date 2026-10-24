import { AlertCircle, Database } from "lucide-react";

import { engineLabel, engineNote, fmtMs } from "@/lib/format";
import type { ZetarisView } from "@/lib/run-state";
import { Badge } from "@/components/ui/badge";

export function ZetarisPanel({ runs }: { runs: ZetarisView[] }) {
  if (!runs.length) {
    return <p className="text-[13px] text-muted">No engine event yet.</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      {runs.map((run, index) => {
        const isReplay = run.engine.includes("replay");
        const isFallback = run.engine.includes("fallback");
        return (
          <div key={index} className="rounded-md border border-line bg-raised/40 px-3 py-2">
            <div className="flex flex-wrap items-center gap-2">
              <Database className="h-3.5 w-3.5 text-muted" aria-hidden />
              <span className="text-[13px] font-medium text-ink">{engineLabel(run.engine)}</span>
              {isReplay && <Badge tone="warn">replay</Badge>}
              {isFallback && <Badge tone="fail">fallback</Badge>}
              <span className="ml-auto mono text-[11px] text-muted">
                {run.rows !== null ? `${run.rows} row(s)` : "no rows reported"}
                {run.latencyMs !== null ? `, ${fmtMs(run.latencyMs)}` : ""}
              </span>
            </div>
            {engineNote(run.engine) && (
              <p className="mt-1 flex items-center gap-1 text-[11px] text-muted">
                <AlertCircle className="h-3 w-3" aria-hidden />
                {engineNote(run.engine)}
              </p>
            )}
            <p className="mono mt-1 truncate text-[11px] text-muted" title={run.detail}>
              {run.detail}
            </p>
          </div>
        );
      })}
    </div>
  );
}
