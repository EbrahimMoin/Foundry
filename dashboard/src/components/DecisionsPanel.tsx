import { Check, X } from "lucide-react";
import type { DecisionRecord } from "../types";

interface DecisionsPanelProps {
  decisions: DecisionRecord[];
  onResolve: (decisionId: string, action: "approve" | "reject", choice?: unknown) => void;
}

export function DecisionsPanel({ decisions, onResolve }: DecisionsPanelProps) {
  const pending = decisions.filter((d) => d.status === "pending");
  const history = decisions.filter((d) => d.status !== "pending");

  return (
    <div className="space-y-3 text-xs">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold uppercase tracking-wider text-slate-400">
          Decision Inbox ({pending.length} pending)
        </h3>
      </div>

      {pending.length === 0 ? (
        <p className="rounded border border-dashed border-slate-800 p-3 text-slate-500">
          All caught up — no pending human decisions.
        </p>
      ) : (
        <div className="space-y-2">
          {pending.map((dec) => (
            <div
              key={dec.id}
              className="rounded border border-amber-800/60 bg-amber-950/20 p-3 space-y-2"
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold text-amber-200">{dec.title}</span>
                <span className="rounded bg-amber-900/50 px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
                  {dec.category} · {dec.mode}
                </span>
              </div>
              <p className="text-slate-300">{dec.context}</p>
              {dec.confidence !== null && (
                <div className="text-[11px] text-slate-400">
                  Confidence: {(dec.confidence * 100).toFixed(0)}% · Reversibility:{" "}
                  {dec.reversibility ?? "unknown"}
                </div>
              )}
              <div className="flex flex-wrap gap-1.5 pt-1">
                {dec.options.map((opt) => (
                  <button
                    key={opt}
                    onClick={() => onResolve(dec.id, "approve", opt)}
                    className="inline-flex items-center gap-1 rounded bg-emerald-700/80 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-emerald-600"
                  >
                    <Check className="h-3 w-3" />
                    {opt}
                  </button>
                ))}
                <button
                  onClick={() => onResolve(dec.id, "reject")}
                  className="inline-flex items-center gap-1 rounded bg-rose-800/80 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-rose-700"
                >
                  <X className="h-3 w-3" />
                  Reject
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {history.length > 0 && (
        <div className="space-y-1 pt-2">
          <div className="text-[11px] font-medium text-slate-500">Recent Resolved ({history.length})</div>
          {history.slice(0, 5).map((d) => (
            <div
              key={d.id}
              className="flex items-center justify-between rounded border border-slate-800 bg-obsidian px-2.5 py-1.5"
            >
              <span className="truncate text-slate-300">{d.title}</span>
              <span className="ml-2 shrink-0 font-mono text-[10px] text-emerald-400">
                {d.status} ({d.decided_by ?? "-"})
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
