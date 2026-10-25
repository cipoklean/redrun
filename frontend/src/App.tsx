import { Link, Route, Routes } from "react-router-dom";

import { EvalPage } from "@/pages/EvalPage";
import { InvestigatePage } from "@/pages/InvestigatePage";
import { RunPage } from "@/pages/RunPage";

function NotFound() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col items-start gap-3 px-4 py-16">
      <h1 className="text-lg font-semibold">That page does not exist.</h1>
      <p className="text-[13px] text-muted">
        RedRun has three screens: investigate a run, read its verdict, and the
        evaluation the two systems are scored on.
      </p>
      <Link to="/" className="text-[13px] text-accent hover:underline">
        Back to investigate
      </Link>
    </div>
  );
}

export function App() {
  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-line bg-bg/80 backdrop-blur">
        <div className="mx-auto flex w-full max-w-6xl items-center gap-3 px-4 py-3 sm:px-6">
          <Link to="/" className="flex items-center gap-2" aria-label="RedRun home">
            <span
              className="flex h-6 w-6 items-center justify-center rounded-sm bg-accent text-[13px] font-bold text-white"
              aria-hidden
            >
              R
            </span>
            <span className="text-sm font-semibold tracking-tight">RedRun</span>
          </Link>
          <span className="hidden text-[12px] text-muted sm:inline">
            explains a failed CI run, with receipts
          </span>
          <nav className="ml-auto flex items-center gap-3 text-[12px] text-muted">
            <Link to="/" className="transition-colors hover:text-ink">
              Investigate
            </Link>
            <Link to="/eval" className="transition-colors hover:text-ink">
              Eval
            </Link>
            <a
              href="/docs"
              className="hidden transition-colors hover:text-ink sm:inline"
              target="_blank"
              rel="noreferrer"
            >
              API
            </a>
          </nav>
        </div>
      </header>

      <main className="flex-1">
        <Routes>
          <Route path="/" element={<InvestigatePage />} />
          <Route path="/eval" element={<EvalPage />} />
          <Route path="/run/:id" element={<RunPage />} />
          <Route path="/run/:id/verdict" element={<RunPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-3 px-4 py-3 text-[11px] text-muted sm:px-6">
          <span>Evidence first. The model proposes, the checks decide.</span>
          <span className="ml-auto mono">Meterless, Nemotron, Zetaris</span>
        </div>
      </footer>
    </div>
  );
}
