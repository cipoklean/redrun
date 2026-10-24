import { ExternalLink } from "lucide-react";

import type { RunResult } from "@/types/contract";
import { EVIDENCE_KINDS, EVIDENCE_LABELS, type EvidenceItem } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Props {
  evidence: EvidenceItem[];
  cited: string[];
  highlight: string | null;
}

function Card({
  item,
  cited,
  highlight,
}: {
  item: EvidenceItem;
  cited: boolean;
  highlight: boolean;
}) {
  return (
    <article
      id={`evidence-${item.id}`}
      data-evidence-id={item.id}
      className={cn(
        "scroll-mt-24 rounded-md border bg-raised/30 p-3 transition-colors",
        highlight ? "card-flash border-accent/70" : cited ? "border-accent/40" : "border-line",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="ev-chip">{item.id}</span>
        <div className="flex items-center gap-2">
          {cited && (
            <span className="text-[10px] uppercase tracking-[0.12em] text-accent">cited</span>
          )}
          <a
            href={item.source_url}
            target="_blank"
            rel="noreferrer noopener"
            className="text-muted transition-colors hover:text-ink"
            aria-label={`Open source for ${item.id}`}
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        </div>
      </div>
      <h4 className="mt-1.5 text-[13px] font-medium text-ink">{item.title}</h4>
      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-line/60 bg-bg/60 p-2 font-mono text-[11px] leading-relaxed text-muted">
        {item.excerpt}
      </pre>
      <p className="mt-1 text-[11px] text-muted">{fmtTime(item.timestamp)}</p>
    </article>
  );
}

export function EvidenceList({ evidence, cited, highlight }: Props) {
  const byKind = EVIDENCE_KINDS.map((kind) => ({
    kind,
    label: EVIDENCE_LABELS[kind] ?? kind,
    items: evidence.filter((item) => item.kind === kind),
  })).filter((group) => group.items.length > 0);

  if (!byKind.length) {
    return <p className="text-[13px] text-muted">No evidence was collected for this run.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {byKind.map((group) => (
        <section key={group.kind}>
          <h3 className="label mb-2">
            {group.label}
            <span className="ml-1 text-muted/70">{group.items.length}</span>
          </h3>
          <div className="grid gap-2 lg:grid-cols-2">
            {group.items.map((item) => (
              <Card
                key={item.id}
                item={item}
                cited={cited.includes(item.id)}
                highlight={highlight === item.id}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
