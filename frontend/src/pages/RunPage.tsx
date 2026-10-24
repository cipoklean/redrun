import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, Loader2, RotateCcw, TriangleAlert } from "lucide-react";

import type { RunResult } from "@/types/contract";
import { fetchResult, replayEvents, startRun, type RunHandle } from "@/lib/api";
import { applyEvent, initLiveState, type LiveState } from "@/lib/run-state";
import { useEvidenceJump } from "@/lib/use-evidence-jump";
import { causeLabel, causeTone, engineLabel, statusTone } from "@/lib/format";
import { normalizeChecks } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Pipeline } from "@/components/Pipeline";
import { StepList, TokenBar } from "@/components/StepList";
import { SkepticChecks } from "@/components/SkepticChecks";
import { ZetarisPanel } from "@/components/ZetarisPanel";
import { VerdictView } from "@/components/VerdictView";
import { EvidenceList } from "@/components/EvidenceList";
import { Timeline } from "@/components/Timeline";
import { RawEvents } from "@/components/RawEvents";

type Phase = "starting" | "streaming" | "waiting" | "done" | "error" | "missing";

export function RunPage() {
  const { id = "new" } = useParams();
  const [params] = useSearchParams();
  const [state, setState] = useState<LiveState>(initLiveState);
  const [result, setResult] = useState<RunResult | null>(null);
  const [phase, setPhase] = useState<Phase>("starting");
  const [error, setError] = useState<string | null>(null);
  const [rawOpen, setRawOpen] = useState(false);
  const rawRef = useRef<HTMLDivElement | null>(null);
  const { highlight, jump: jumpToEvidence } = useEvidenceJump();

  const traceRef = useRef<string | null>(id === "new" ? null : id);
  const startedRef = useRef(false);
  const aliveRef = useRef(true);
  const handleRef = useRef<RunHandle | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const finish = useCallback(async (traceId: string, attempts = 0): Promise<void> => {
    const stored = await fetchResult(traceId);
    if (!aliveRef.current) return;
    if (stored) {
      setResult(stored);
      setPhase("done");
      stopPolling();
      return;
    }
    setPhase("waiting");
    if (attempts >= 90) {
      setPhase("missing");
      return;
    }
    // No result on disk yet: keep asking, the run may still be writing.
    pollRef.current = window.setTimeout(() => {
      void finish(traceId, attempts + 1);
    }, 2000) as unknown as number;
  }, [stopPolling]);

  const runFresh = useCallback(async () => {
    setPhase("streaming");
    try {
      const handle = await startRun(
        {
          target: params.get("target") ?? undefined,
          sample: params.get("sample") ?? undefined,
        },
        {
          onTrace: (traceId) => {
            traceRef.current = traceId;
            window.history.replaceState(null, "", `/run/${traceId}`);
          },
          onEvent: (event) => setState((previous) => applyEvent(previous, event)),
          onServerError: (detail) => {
            setError(detail);
            setPhase("error");
          },
        },
      );
      handleRef.current = handle;
      await handle.done;
      if (!aliveRef.current) return;
      const traceId = traceRef.current;
      if (!traceId) {
        setError("the server did not return a trace id");
        setPhase("error");
        return;
      }
      await finish(traceId);
    } catch (cause) {
      if (!aliveRef.current) return;
      setError((cause as Error).message);
      setPhase("error");
    }
  }, [finish, params]);

  const resume = useCallback(
    async (traceId: string) => {
      setPhase("starting");
      const stored = await fetchResult(traceId);
      if (!aliveRef.current) return;
      if (stored) {
        setResult(stored);
        setPhase("done");
        return;
      }
      setPhase("waiting");
      // Replay whatever is on disk, then keep checking for the stored result.
      const handle = replayEvents(traceId, {
        onEvent: (event) => setState((previous) => applyEvent(previous, event)),
      });
      handleRef.current = { abort: handle.abort, done: handle.done };
      await handle.done;
      if (!aliveRef.current) return;
      await finish(traceId);
    },
    [finish],
  );

  useEffect(() => {
    aliveRef.current = true;
    if (id === "new") {
      if (startedRef.current) return;
      startedRef.current = true;
      void runFresh();
    } else {
      void resume(id);
    }
    return () => {
      aliveRef.current = false;
      handleRef.current?.abort();
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  function viewTrace() {
    setRawOpen(true);
    window.setTimeout(() => rawRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
  }

  const label = params.get("sample")
    ? `sample:${params.get("sample")}`
    : (params.get("target") ?? traceRef.current ?? id);

  const running = phase === "starting" || phase === "streaming" || phase === "waiting";
  const verdict = result?.verdict ?? null;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-center gap-3">
        <Button asChild variant="ghost" size="sm">
          <Link to="/">
            <ArrowLeft className="h-3.5 w-3.5" /> Investigate
          </Link>
        </Button>
        <span className="mono truncate text-[12px] text-muted">{label}</span>
        {running && (
          <Badge tone="accent">
            <Loader2 className="h-3 w-3 animate-spin" />
            {phase === "waiting" ? "run finished, loading the stored result" : "running"}
          </Badge>
        )}
        {phase === "done" && verdict && (
          <Badge tone={causeTone(verdict.cause)}>{causeLabel(verdict.cause)}</Badge>
        )}
        {phase === "done" && result && (
          <Badge tone={statusTone(result.skeptic?.final_status ?? "skipped")}>
            skeptic {result.skeptic?.final_status ?? "skipped"}
          </Badge>
        )}
        <span className="mono ml-auto text-[11px] text-muted">
          {state.engine ? engineLabel(state.engine) : "engine pending"}
        </span>
      </header>

      {phase === "error" && (
        <Card className="border-fail/40 bg-fail/5 p-4">
          <div className="flex items-start gap-2">
            <TriangleAlert className="mt-0.5 h-4 w-4 text-fail" />
            <div>
              <p className="text-sm font-medium text-ink">The run could not be started.</p>
              <p className="mt-1 text-[13px] text-muted">{error}</p>
              <Button asChild variant="outline" size="sm" className="mt-3">
                <Link to="/">
                  <RotateCcw className="h-3.5 w-3.5" /> Try another target
                </Link>
              </Button>
            </div>
          </div>
        </Card>
      )}

      {phase === "missing" && (
        <Card className="p-4">
          <p className="text-sm font-medium">No stored result for {traceRef.current}.</p>
          <p className="mt-1 text-[13px] text-muted">
            The trace may have been cleared. Run the investigation again from the start page.
          </p>
        </Card>
      )}

      {(running || phase === "done") && (
        <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
          <div className="flex flex-col gap-3">
            <Card className="p-4">
              <h2 className="label mb-3">pipeline</h2>
              <Pipeline state={state} />
            </Card>
            <TokenBar state={state} metrics={result?.metrics?.markovian ?? null} />
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            {running && (
              <>
                <Card className="p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <h2 className="text-sm font-semibold">Investigator steps</h2>
                    <span className="text-[11px] text-muted">
                      {state.steps.length} step{state.steps.length === 1 ? "" : "s"}
                      {state.revisions ? `, ${state.revisions} revision` : ""}
                    </span>
                  </div>
                  <StepList state={state} />
                </Card>

                <div className="grid gap-4">
                  <Card className="p-4">
                    <h2 className="mb-2 text-sm font-semibold">Cross-source query</h2>
                    <ZetarisPanel runs={state.zetaris} />
                  </Card>
                  <Card className="p-4">
                    <h2 className="mb-2 text-sm font-semibold">Skeptic checks</h2>
                    <SkepticChecks checks={state.checks} />
                  </Card>
                </div>
              </>
            )}

            {phase === "done" && result && (
              <Tabs defaultValue="verdict">
                <TabsList>
                  <TabsTrigger value="verdict">Verdict</TabsTrigger>
                  <TabsTrigger value="timeline">Change timeline</TabsTrigger>
                </TabsList>

                <TabsContent value="verdict">
                  <div className="flex flex-col gap-4">
                    <VerdictView result={result} onCite={jumpToEvidence} onViewTrace={viewTrace} />
                    <Card className="p-4">
                      <div className="mb-2 flex items-center justify-between">
                        <h3 className="text-sm font-semibold">Evidence</h3>
                        <span className="text-[11px] text-muted">
                          {result.evidence?.length ?? 0} item(s), redacted at collection
                        </span>
                      </div>
                      <EvidenceList
                        evidence={result.evidence ?? []}
                        cited={verdict?.cited_evidence_ids ?? []}
                        highlight={highlight}
                      />
                    </Card>
                  </div>
                </TabsContent>

                <TabsContent value="timeline">
                  <Timeline result={result} />
                </TabsContent>
              </Tabs>
            )}

            {(state.events.length > 0 || result) && (
              <RawEvents
                events={state.events}
                open={rawOpen}
                onToggle={() => setRawOpen((open) => !open)}
                innerRef={rawRef}
              />
            )}
          </div>
        </div>
      )}

      {phase === "starting" && (
        <p className="text-[12px] text-muted">Loading the stored run...</p>
      )}
    </div>
  );
}
