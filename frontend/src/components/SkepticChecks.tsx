import { Badge } from "@/components/ui/badge";
import type { NormalizedCheck } from "@/lib/types";
import { statusTone } from "@/lib/format";
import { cn } from "@/lib/utils";

export function SkepticChecks({ checks }: { checks: NormalizedCheck[] }) {
  if (!checks.length) {
    return <p className="text-[13px] text-muted">The skeptic has not run yet.</p>;
  }
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {checks.map((check) => (
        <div
          key={check.check}
          className={cn(
            "rounded-md border px-3 py-2",
            check.status === "fail"
              ? "border-fail/40 bg-fail/5"
              : check.status === "pass"
                ? "border-pass/30 bg-pass/5"
                : "border-line bg-raised/40",
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="mono text-[12px] text-ink">{check.check}</span>
            <Badge tone={statusTone(check.status)}>{check.status}</Badge>
          </div>
          <p className="mt-1 text-[12px] leading-snug text-muted">{check.detail}</p>
        </div>
      ))}
    </div>
  );
}
