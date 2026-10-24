import { useEffect, useState } from "react";
import { Github, RefreshCw, Server, Sparkles } from "lucide-react";

import { fetchProviderStatus, type ProviderStatus } from "@/lib/api";
import { statusTone } from "@/lib/format";
import { Badge, Dot } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface StripProps {
  onOpenSettings?: () => void;
  className?: string;
}

export function ProviderStrip({ onOpenSettings, className }: StripProps) {
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      setStatus(await fetchProviderStatus());
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-lg border border-line bg-panel/70 px-3 py-2",
        className,
      )}
      aria-label="Provider status"
    >
      {error && <Badge tone="fail">status unavailable</Badge>}
      {loading && !status && <span className="text-xs text-muted">checking providers...</span>}

      {status && (
        <>
          <span className="flex items-center gap-2 pr-1">
            <Sparkles className="h-3.5 w-3.5 text-muted" aria-hidden />
            <span className="label">LLM</span>
            <Badge tone={statusTone(status.llm.status)}>
              <Dot tone={statusTone(status.llm.status)} />
              {status.llm.status}
            </Badge>
            <span className="mono text-[11px] text-muted">{status.llm.provider}</span>
          </span>

          <span className="h-4 w-px bg-line" aria-hidden />

          <span className="flex items-center gap-2 pr-1">
            <Server className="h-3.5 w-3.5 text-muted" aria-hidden />
            <span className="label">Zetaris</span>
            <Badge tone={statusTone(status.zetaris.status)}>
              <Dot tone={statusTone(status.zetaris.status)} />
              {status.zetaris.status}
            </Badge>
            {status.zetaris.url && (
              <span className="mono hidden text-[11px] text-muted lg:inline">{status.zetaris.url}</span>
            )}
          </span>

          <span className="h-4 w-px bg-line" aria-hidden />

          <span className="flex items-center gap-2">
            <Github className="h-3.5 w-3.5 text-muted" aria-hidden />
            <span className="label">GitHub token</span>
            <Badge tone={status.github.token_set ? "pass" : "muted"}>
              {status.github.token_set ? "present" : "not set"}
            </Badge>
          </span>

          {status.sample_mode && <Badge tone="accent">sample mode</Badge>}
        </>
      )}

      <div className="ml-auto flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={() => void load()} aria-label="Refresh provider status">
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          Refresh
        </Button>
        {onOpenSettings && (
          <Button variant="outline" size="sm" onClick={onOpenSettings}>
            Settings
          </Button>
        )}
      </div>
    </div>
  );
}
