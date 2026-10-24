import * as React from "react";

import { cn } from "@/lib/utils";
import { type Tone } from "@/lib/format";

const toneClass: Record<Tone, string> = {
  accent: "border-accent/40 bg-accent/10 text-accent",
  pass: "border-pass/40 bg-pass/10 text-pass",
  fail: "border-fail/40 bg-fail/10 text-fail",
  warn: "border-warn/40 bg-warn/10 text-warn",
  muted: "border-line bg-raised text-muted",
};

export function Badge({
  tone = "muted",
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium uppercase tracking-[0.1em]",
        toneClass[tone],
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}

export function Dot({ tone = "muted" }: { tone?: Tone }) {
  const color =
    tone === "pass"
      ? "bg-pass"
      : tone === "fail"
        ? "bg-fail"
        : tone === "warn"
          ? "bg-warn"
          : tone === "accent"
            ? "bg-accent"
            : "bg-muted/60";
  return <span className={cn("inline-block h-1.5 w-1.5 rounded-full", color)} aria-hidden />;
}
