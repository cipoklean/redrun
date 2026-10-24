import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import type { Event as RunEvent } from "@/types/contract";
import { fmtTime } from "@/lib/format";

export function RawEvents({
  events,
  open,
  onToggle,
  innerRef,
}: {
  events: RunEvent[];
  open: boolean;
  onToggle: () => void;
  innerRef?: React.Ref<HTMLDivElement>;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);

  return (
    <section className="rounded-lg border border-line bg-panel" ref={innerRef}>
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2 px-4 py-3 text-left"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <span className="text-sm font-semibold">Raw event log</span>
        <span className="mono ml-auto text-[11px] text-muted">{events.length} events</span>
      </button>
      {open && (
        <div className="max-h-80 overflow-auto border-t border-line px-2 py-2">
          {events.length === 0 && <p className="px-2 text-[12px] text-muted">No events yet.</p>}
          <ul className="flex flex-col">
            {events.map((event, index) => (
              <li key={index} className="border-b border-line/50 last:border-0">
                <button
                  type="button"
                  className="flex w-full items-baseline gap-2 px-2 py-1 text-left hover:bg-raised/50"
                  onClick={() => setExpanded(expanded === index ? null : index)}
                >
                  <span className="mono w-[68px] shrink-0 text-[10px] text-muted">
                    {fmtTime(event.ts)}
                  </span>
                  <span className="mono w-[92px] shrink-0 text-[11px] text-accent/90">
                    {event.agent}
                  </span>
                  <span className="mono w-[68px] shrink-0 text-[10px] uppercase text-muted">
                    {event.type}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[12px] text-ink/90">
                    {event.message}
                  </span>
                </button>
                {expanded === index && event.data && (
                  <pre className="mx-2 mb-2 overflow-auto rounded-sm border border-line/60 bg-bg/70 p-2 font-mono text-[11px] text-muted">
                    {JSON.stringify(event.data, null, 2)}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
