import { useEffect, useState } from "react";

import { Badge, Dot } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchEvalResults, type EvalObservation, type EvalResults, type EvalSummary } from "@/lib/api";

const SPLITS = ["dev", "holdout", "all"] as const;

function counts(hits: number | null | undefined, total: number): string {
  if (hits === null || hits === undefined || !total) return "n/a";
  return `${hits}/${total}`;
}

function percent(value: number | null): string {
  if (value === null || value === undefined) return "n/a";
  return `${(value * 100).toFixed(1)}%`;
}

function score(observation: EvalObservation | null) {
  if (!observation) return { label: "not scored", tone: "muted" as const };
  return {
    label: observation.cause ?? "none",
    tone: observation.category_ok ? ("pass" as const) : ("fail" as const),
  };
}

function TotalsTable({ summaries }: { summaries: EvalResults["summaries"] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="label">
            <th className="px-3 py-2 text-left">split</th>
            <th className="px-3 py-2 text-left">cases</th>
            <th className="px-3 py-2 text-left">agent cause</th>
            <th className="px-3 py-2 text-left">agent keywords</th>
            <th className="px-3 py-2 text-left">citations</th>
            <th className="px-3 py-2 text-left">baseline cause</th>
            <th className="px-3 py-2 text-left">baseline keywords</th>
          </tr>
        </thead>
        <tbody>
          {SPLITS.map((split) => {
            const entry = summaries[split];
            if (!entry) return null;
            const agent: EvalSummary = entry.agent;
            const baseline: EvalSummary = entry.baseline;
            return (
              <tr key={split} className="border-t border-line">
                <td className="px-3 py-2 font-medium text-ink">{split}</td>
                <td className="px-3 py-2 text-muted">{agent.cases}</td>
                <td className="px-3 py-2 text-muted">
                  {counts(agent.cause_hits, agent.cases)}{" "}
                  <span className="text-[11px]">({percent(agent.cause_accuracy)})</span>
                </td>
                <td className="px-3 py-2 text-muted">
                  {counts(agent.keyword_hits, agent.cases)}{" "}
                  <span className="text-[11px]">({percent(agent.keyword_accuracy)})</span>
                </td>
                <td className="px-3 py-2 text-muted">
                  {agent.citation_pass_rate === null
                    ? "n/a"
                    : `${counts(agent.citation_pass, agent.citation_evaluated)} (${percent(
                        agent.citation_pass_rate,
                      )})`}
                  {agent.guard_interventions > 0 && (
                    <span className="ml-2 text-[11px] text-warn">
                      {agent.guard_interventions} guard rejection
                      {agent.guard_interventions === 1 ? "" : "s"}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-muted">
                  {counts(baseline.cause_hits, baseline.cases)}{" "}
                  <span className="text-[11px]">({percent(baseline.cause_accuracy)})</span>
                </td>
                <td className="px-3 py-2 text-muted">
                  {counts(baseline.keyword_hits, baseline.cases)}{" "}
                  <span className="text-[11px]">({percent(baseline.keyword_accuracy)})</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function EvalPage() {
  const [results, setResults] = useState<EvalResults | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    fetchEvalResults()
      .then((payload) => {
        if (live) setResults(payload);
      })
      .catch((problem: Error) => {
        if (live) setError(problem.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);

  if (loading) {
    return <p className="mx-auto max-w-6xl px-4 py-10 text-[13px] text-muted">Loading results...</p>;
  }
  if (error) {
    return <p className="mx-auto max-w-6xl px-4 py-10 text-[13px] text-fail">{error}</p>;
  }
  if (!results) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-16">
        <h1 className="text-lg font-semibold text-ink">No evaluation has been run yet.</h1>
        <p className="mt-2 text-[13px] text-muted">
          Run{" "}
          <code className="mono rounded bg-raised px-1.5 py-0.5">
            python -m redrun eval --split all --mode replay --baseline
          </code>{" "}
          and this screen reads its output.
        </p>
      </div>
    );
  }

  const { meta, rows, summaries, pending, baseline_pending } = results;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-6 sm:px-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-lg font-semibold text-ink">Evaluation</h1>
        <Badge tone="muted">{meta.mode}</Badge>
        <span className="mono text-[12px] text-muted">
          {meta.case_count} cases · split {meta.split} · seed {meta.seed} · {meta.timestamp}
        </span>
      </div>

      <p className="max-w-4xl text-[13px] text-muted">
        The agent is the Markovian investigator, the skeptic, the citation guard and
        computed confidence. The baseline is one plain{" "}
        <span className="mono">{meta.baseline_model ?? "fast lane"}</span> call on the last{" "}
        {meta.baseline_lines} redacted log lines and nothing else. The holdout split was
        drawn from the seed before any of these runs happened.
      </p>

      <Card>
        <CardHeader>
          <CardTitle>Totals</CardTitle>
        </CardHeader>
        <CardBody className="pt-0">
          <TotalsTable summaries={summaries} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Per case</CardTitle>
          <span className="text-[11px] text-muted">
            expected vs returned, agent and baseline
          </span>
        </CardHeader>
        <CardBody className="pt-0">
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="label">
                  <th className="px-3 py-2 text-left">case</th>
                  <th className="px-3 py-2 text-left">expected</th>
                  <th className="px-3 py-2 text-left">split</th>
                  <th className="px-3 py-2 text-left">source</th>
                  <th className="px-3 py-2 text-left">agent</th>
                  <th className="px-3 py-2 text-left">keywords</th>
                  <th className="px-3 py-2 text-left">conf</th>
                  <th className="px-3 py-2 text-left">baseline</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const agent = score(row.agent);
                  const baseline = score(row.baseline);
                  return (
                    <tr key={row.id} className="border-t border-line align-top">
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <Dot tone={row.agent ? (row.agent.category_ok ? "pass" : "fail") : "warn"} />
                          <span className="mono text-[12px] text-ink">{row.id}</span>
                        </div>
                      </td>
                      <td className="mono px-3 py-2 text-[12px] text-muted">{row.expected_cause}</td>
                      <td className="px-3 py-2 text-muted">{row.split}</td>
                      <td className="px-3 py-2 text-muted">
                        {row.constructed ? "constructed" : "public"}
                      </td>
                      <td className="px-3 py-2">
                        {row.agent ? (
                          <Badge tone={agent.tone}>{agent.label}</Badge>
                        ) : (
                          <span className="text-[12px] text-warn">pending ({row.path})</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-[12px] text-muted">
                        {row.agent ? (
                          row.agent.keywords_ok ? (
                            "all"
                          ) : (
                            <span className="text-fail">
                              missing {row.agent.keywords_missing.join(", ")}
                            </span>
                          )
                        ) : (
                          "n/a"
                        )}
                      </td>
                      <td className="px-3 py-2 text-[12px] text-muted">
                        {row.agent?.confidence === null || row.agent?.confidence === undefined
                          ? "n/a"
                          : row.agent.confidence.toFixed(2)}
                      </td>
                      <td className="px-3 py-2">
                        {row.baseline ? (
                          <Badge tone={baseline.tone}>{baseline.label}</Badge>
                        ) : (
                          <span className="text-[12px] text-muted">not recorded</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>

      {pending.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Pending a live recording</CardTitle>
          </CardHeader>
          <CardBody className="pt-0">
            <ul className="flex flex-col gap-1 text-[12px] text-muted">
              {pending.map((item) => (
                <li key={item.id}>
                  <span className="mono text-ink">{item.id}</span> ({item.split}): {item.reason}
                  {item.rules_cause ? ` · rules-only diagnostic: ${item.rules_cause}` : ""}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      {baseline_pending.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Without a recorded baseline</CardTitle>
          </CardHeader>
          <CardBody className="pt-0">
            <ul className="flex flex-col gap-1 text-[12px] text-muted">
              {baseline_pending.map((item) => (
                <li key={item.id}>
                  <span className="mono text-ink">{item.id}</span> ({item.split}): {item.reason}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
