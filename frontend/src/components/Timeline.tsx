import type { RunResult } from "@/types/contract";
import type { EvidenceItem } from "@/lib/types";
import { fmtShortTime, fmtTime } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Item {
  id: string;
  label: string;
  detail: string;
  at: number;
  kind: "run" | "commit" | "dependency" | "outage";
  cited: boolean;
  href: string;
}

function releaseTime(item: EvidenceItem): string | null {
  const meta = item.metadata ?? {};
  const value = meta["released_at"];
  return typeof value === "string" ? value : null;
}

function buildItems(result: RunResult): { items: Item[]; start: number; end: number } {
  const cited = new Set(result.verdict?.cited_evidence_ids ?? []);
  const toTs = (value?: string | null): number => {
    if (!value) return Number.NaN;
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? Number.NaN : parsed;
  };

  const startTs = toTs(result.last_green_run?.created_at);
  const endTs = toTs(result.failed_run.created_at);
  const fallback = Date.now();
  const start = Number.isNaN(startTs) ? fallback : startTs;
  const end = Number.isNaN(endTs) ? start : endTs;

  const items: Item[] = [];

  const failedAt = end;
  items.push({
    id: result.failed_run.head_sha.slice(0, 8),
    label: "Failed run",
    detail: `${result.failed_run.workflow_name} ${result.failed_run.conclusion}`,
    at: failedAt,
    kind: "run",
    cited: false,
    href: "",
  });

  if (result.last_green_run) {
    items.push({
      id: result.last_green_run.head_sha.slice(0, 8),
      label: "Last green run",
      detail: `${result.last_green_run.workflow_name} ${result.last_green_run.conclusion}`,
      at: start,
      kind: "run",
      cited: false,
      href: "",
    });
  }

  for (const item of result.evidence ?? []) {
    if (item.kind === "commit") {
      const at = toTs(item.timestamp);
      if (Number.isNaN(at)) continue;
      items.push({
        id: item.id,
        label: item.title,
        detail: "commit",
        at,
        kind: "commit",
        cited: cited.has(item.id),
        href: item.source_url,
      });
    }
    if (item.kind === "dependency") {
      const released = releaseTime(item);
      const at = toTs(released ?? item.timestamp);
      if (Number.isNaN(at)) continue;
      items.push({
        id: item.id,
        label: item.title,
        detail: released ? `released ${fmtTime(released)}` : "dependency change",
        at,
        kind: "dependency",
        cited: cited.has(item.id),
        href: item.source_url,
      });
    }
    if (item.kind === "outage") {
      const at = toTs(item.timestamp);
      if (Number.isNaN(at)) continue;
      items.push({
        id: item.id,
        label: item.title,
        detail: "GitHub incident",
        at,
        kind: "outage",
        cited: cited.has(item.id),
        href: item.source_url,
      });
    }
  }

  items.sort((a, b) => a.at - b.at);
  return { items, start, end };
}

const KIND_COLOR: Record<Item["kind"], string> = {
  run: "bg-muted",
  commit: "bg-accent",
  dependency: "bg-warn",
  outage: "bg-fail",
};

export function Timeline({ result }: { result: RunResult }) {
  const { items, start, end } = buildItems(result);
  const span = Math.max(end - start, 1);

  return (
    <div className="rounded-lg border border-line bg-panel p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Change timeline</h3>
        <span className="mono text-[11px] text-muted">
          {fmtShortTime(new Date(start).toISOString())} to {fmtShortTime(new Date(end).toISOString())}
        </span>
      </div>

      <div className="relative mt-6 h-10">
        <div className="absolute left-0 right-0 top-4 h-px bg-line" />
        {items.map((item) => {
          const pct = Math.min(100, Math.max(0, ((item.at - start) / span) * 100));
          return (
            <button
              key={`${item.id}-${item.at}`}
              type="button"
              className="absolute top-2 -translate-x-1/2"
              style={{ left: `${pct}%` }}
              title={`${item.label} at ${fmtTime(new Date(item.at).toISOString())}`}
              aria-label={`${item.label} at ${fmtTime(new Date(item.at).toISOString())}`}
            >
              <span
                className={cn(
                  "block h-3 w-3 rounded-full border-2",
                  KIND_COLOR[item.kind],
                  item.cited ? "border-accent ring-2 ring-accent/30" : "border-bg",
                )}
              />
            </button>
          );
        })}
      </div>

      <ul className="mt-2 divide-y divide-line/70">
        {items.map((item) => (
          <li key={`${item.id}-row`} className="flex items-center gap-3 py-1.5">
            <span className={cn("h-1.5 w-1.5 rounded-full", KIND_COLOR[item.kind])} aria-hidden />
            <span className="mono w-16 shrink-0 text-[11px] text-muted">
              {fmtShortTime(new Date(item.at).toISOString())}
            </span>
            <span className={cn("min-w-0 flex-1 truncate text-[13px]", item.cited ? "text-ink" : "text-muted")}>
              {item.label}
            </span>
            <span className="hidden shrink-0 text-[11px] text-muted sm:inline">{item.detail}</span>
            {item.cited && (
              <span className="text-[10px] uppercase tracking-[0.12em] text-accent">cited</span>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-muted">
        Cited items are ringed in red. Times come from the evidence, not from the model.
      </p>
    </div>
  );
}
