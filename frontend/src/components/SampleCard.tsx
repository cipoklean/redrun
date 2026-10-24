import { ArrowRight, Loader2 } from "lucide-react";

import type { SampleSummary } from "@/lib/api";
import { causeLabel, causeTone } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface Props {
  sample: SampleSummary;
  onRun: (name: string) => void;
  busy: boolean;
  active: boolean;
}

export function SampleCard({ sample, onRun, busy, active }: Props) {
  return (
    <button
      type="button"
      onClick={() => onRun(sample.name)}
      disabled={busy}
      className={cn(
        "group flex h-full flex-col gap-2 rounded-lg border border-line bg-panel p-3 text-left transition-colors",
        "hover:border-accent/50 hover:bg-raised disabled:opacity-60",
        active && "border-accent/70",
      )}
      aria-label={`Run sample ${sample.title}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="mono text-[11px] text-muted">{sample.name}</span>
        <Badge tone={causeTone(sample.category)}>{causeLabel(sample.category)}</Badge>
      </div>
      <span className="text-sm font-medium text-ink">{sample.title}</span>
      <p className="text-[13px] leading-snug text-muted">{sample.description}</p>
      <span className="mt-auto flex items-center gap-1 pt-1 text-[12px] text-accent opacity-0 transition-opacity group-hover:opacity-100">
        {busy && active ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> running
          </>
        ) : (
          <>
            Run this sample <ArrowRight className="h-3.5 w-3.5" />
          </>
        )}
      </span>
    </button>
  );
}
