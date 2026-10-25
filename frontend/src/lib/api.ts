import type { Event as RunEvent, RunResult } from "@/types/contract";

export type LlmStatus = "online" | "unauthorized" | "offline" | "sample";
export type ZetarisStatus = "online" | "unauthorized" | "offline";

export interface ProviderStatus {
  llm: {
    provider: string;
    status: LlmStatus;
    base_url: string | null;
    models: { fast: string; deep: string; judge: string };
    token_set: boolean;
  };
  zetaris: {
    status: ZetarisStatus;
    configured: boolean;
    enabled: boolean;
    url: string | null;
  };
  github: { token_set: boolean };
  sample_mode: boolean;
  samples: number;
}

export interface ProviderTest {
  llm: {
    provider: string;
    status: LlmStatus;
    base_url: string | null;
    model_count: number;
    models: string[];
    latency_ms: number;
    token_set: boolean;
  };
  zetaris: {
    status: ZetarisStatus;
    configured: boolean;
    enabled: boolean;
    url: string | null;
    latency_ms: number;
  };
  summary: string;
}

export interface SampleSummary {
  name: string;
  title: string;
  category: string;
  description: string;
  order: number;
}

/** One system's score for one split, as written by the eval runner. */
export interface EvalSummary {
  system: string;
  cases: number;
  cause_hits: number;
  cause_accuracy: number | null;
  keyword_hits: number;
  keyword_accuracy: number | null;
  citation_evaluated: number;
  citation_pass: number | null;
  citation_pass_rate: number | null;
  guard_interventions: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  tokens_in: number;
  tokens_out: number;
  calibration: {
    correct: number | null;
    wrong: number | null;
    correct_cases: number;
    wrong_cases: number;
  };
  confusion: Record<string, Record<string, number>>;
}

export interface EvalObservation {
  cause: string | null;
  root_cause: string;
  next_step: string;
  confidence: number | null;
  citation_ok: boolean | null;
  category_ok: boolean;
  keywords_ok: boolean;
  keywords_missing: string[];
  latency_ms?: number;
  tokens_in?: number;
  tokens_out?: number;
  guard_intervened?: boolean;
  steps?: number;
  stop_reason?: string | null;
  engine?: string | null;
  model?: string;
}

export interface EvalRow {
  id: string;
  url: string;
  expected_cause: string;
  root_cause_keywords: string[];
  split: string;
  constructed: boolean;
  notes: string;
  path: string;
  path_reason: string;
  agent: EvalObservation | null;
  baseline: EvalObservation | null;
  baseline_reason?: string;
  diagnostic: Record<string, unknown>;
}

export interface EvalResults {
  meta: {
    timestamp: string;
    mode: string;
    split: string;
    seed: number;
    case_count: number;
    baseline_model: string | null;
    baseline_lines: number;
    git_sha: string | null;
    notes: string;
  };
  rows: EvalRow[];
  summaries: Record<string, { agent: EvalSummary; baseline: EvalSummary }>;
  pending: { id: string; split: string; path: string; reason: string; rules_cause?: string }[];
  baseline_pending: { id: string; split: string; reason: string }[];
}

export async function fetchEvalResults(): Promise<EvalResults | null> {
  try {
    return await getJson<EvalResults>("/eval");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* not JSON, fall through */
  }
  return `${response.status} ${response.statusText}`.trim();
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { accept: "application/json" } });
  if (!response.ok) throw new ApiError(await readError(response), response.status);
  return (await response.json()) as T;
}

export function fetchProviderStatus(): Promise<ProviderStatus> {
  return getJson<ProviderStatus>("/provider/status");
}

export function testConnection(): Promise<ProviderTest> {
  return getJson<ProviderTest>("/provider/test");
}

export async function fetchSamples(): Promise<SampleSummary[]> {
  const body = await getJson<{ samples: SampleSummary[] }>("/samples");
  return body.samples;
}

/** The stored result for a finished run, or null when it is not written yet. */
export async function fetchResult(traceId: string): Promise<RunResult | null> {
  try {
    return await getJson<RunResult>(`/run/${traceId}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export interface RunHandlers {
  onTrace?: (traceId: string) => void;
  onEvent?: (event: RunEvent) => void;
  onServerError?: (detail: string) => void;
}

export interface RunHandle {
  abort: () => void;
  done: Promise<void>;
}

interface StreamFrame {
  event: string;
  data: unknown;
}

function parseFrame(block: string): StreamFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const raw of block.split("\n")) {
    const line = raw.trimEnd();
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

/** POST /run/stream and hand each parsed event to the caller as it arrives. */
export async function startRun(
  body: { target?: string; sample?: string },
  handlers: RunHandlers,
): Promise<RunHandle> {
  const controller = new AbortController();
  const response = await fetch("/run/stream", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal: controller.signal,
  });

  if (!response.ok) {
    throw new ApiError(await readError(response), response.status);
  }

  const traceId = response.headers.get("x-redrun-trace");
  if (traceId) handlers.onTrace?.(traceId);

  const done = (async () => {
    const reader = response.body?.getReader();
    if (!reader) return;
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { value, done: finished } = await reader.read();
        if (finished) break;
        buffer += decoder.decode(value, { stream: true });
        let split = buffer.indexOf("\n\n");
        while (split >= 0) {
          const block = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          const frame = parseFrame(block);
          if (frame) {
            if (frame.event === "error") {
              const detail = (frame.data as { detail?: string }).detail ?? "stream error";
              handlers.onServerError?.(detail);
            } else {
              handlers.onEvent?.(frame.data as RunEvent);
            }
          }
          split = buffer.indexOf("\n\n");
        }
      }
    } catch (error) {
      if ((error as Error).name !== "AbortError") handlers.onServerError?.((error as Error).message);
    }
  })();

  return { abort: () => controller.abort(), done };
}

/** Subscribe to a finished (or in progress) trace as SSE. */
export function replayEvents(
  traceId: string,
  handlers: RunHandlers,
): RunHandle & { done: Promise<void> } {
  const controller = new AbortController();
  const source = new EventSource(`/run/${traceId}/events`);

  const done = new Promise<void>((resolve) => {
    source.onmessage = (message) => {
      try {
        handlers.onEvent?.(JSON.parse(message.data) as RunEvent);
      } catch {
        /* ignore a malformed frame rather than kill the stream */
      }
    };
    source.onerror = () => {
      source.close();
      resolve();
    };
    source.addEventListener("error", () => {
      source.close();
      resolve();
    });
    controller.signal.addEventListener("abort", () => {
      source.close();
      resolve();
    });
  });

  return { abort: () => controller.abort(), done };
}
