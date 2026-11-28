import { useState, type FormEvent } from "react";
import { ArrowUp, Pause, Play, Square } from "lucide-react";
import type { AutonomyConfig, CostsSummaryResponse, RunRecord } from "../types";

export type InspectorSection = "decisions" | "models" | "costs" | "outbox";

interface CommandDockProps {
  run: RunRecord | null;
  autonomy: AutonomyConfig | null;
  costs: CostsSummaryResponse | null;
  lastSeq: number;
  wsConnected: boolean;
  onToggleInspector: (section: InspectorSection) => void;
  onAutonomyChange: (preset: AutonomyConfig["preset"]) => Promise<void>;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
  onStop: () => Promise<void>;
  onPostNote: (text: string, kind: "note" | "gotcha") => Promise<void>;
}

const AUTONOMY_PRESETS: AutonomyConfig["preset"][] = [
  "autopilot",
  "balanced",
  "architect",
  "full_control",
  "benchmark",
];

export function CommandDock({
  run,
  autonomy,
  costs,
  lastSeq,
  wsConnected,
  onToggleInspector,
  onAutonomyChange,
  onPause,
  onResume,
  onStop,
  onPostNote,
}: CommandDockProps) {
  const [input, setInput] = useState("");
  const [kind, setKind] = useState<"note" | "gotcha">("note");
  const [submitting, setSubmitting] = useState(false);

  const isPaused = run?.phase === "paused";
  const isTerminal = run?.phase === "stopped" || run?.phase === "completed";

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || !run || submitting) return;
    setSubmitting(true);
    try {
      await onPostNote(trimmed, kind);
      setInput("");
      setKind("note");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="shrink-0 px-6 pb-4 pt-2">
      <div className="max-w-[660px] mx-auto space-y-2">
        {/* Rounded Pill Input with Optional Gotcha Toggle + Sage Send Button */}
        <form
          onSubmit={handleSubmit}
          className="h-[54px] rounded-full border pl-5 pr-2.5 flex items-center gap-2.5 transition-colors focus-within:border-[var(--border-strong)]"
          style={{
            backgroundColor: "var(--bg-surface)",
            borderColor: "var(--border-hairline)",
          }}
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={!run || submitting}
            placeholder={run ? "Guide agents or log a run note…" : "Start a run to log guidance notes…"}
            aria-label="Guide agents or log a run note"
            className="flex-1 min-w-0 bg-transparent font-serif text-[17.5px] outline-none placeholder:text-[var(--ink-muted)] disabled:opacity-60"
            style={{ color: "var(--ink-primary)" }}
          />
          {run && input.trim().length > 0 && (
            <button
              type="button"
              onClick={() => setKind((prev) => (prev === "note" ? "gotcha" : "note"))}
              title="Toggle between run note and gotcha warning"
              className="px-2.5 py-1 rounded-full text-[11px] font-medium shrink-0 transition-colors"
              style={
                kind === "gotcha"
                  ? {
                      backgroundColor: "var(--status-warn-bg)",
                      color: "var(--status-warn)",
                    }
                  : {
                      backgroundColor: "var(--bg-active)",
                      color: "var(--ink-secondary)",
                    }
              }
            >
              {kind === "gotcha" ? "gotcha" : "note"}
            </button>
          )}
          <button
            type="submit"
            disabled={!run || !input.trim() || submitting}
            aria-label="Send guidance"
            className="w-9 h-9 rounded-full flex items-center justify-center shrink-0 transition-colors disabled:opacity-50"
            style={{
              backgroundColor: input.trim() && run ? "var(--accent)" : "var(--accent-pill)",
              color: "#ffffff",
            }}
          >
            <ArrowUp className="w-[18px] h-[18px]" />
          </button>
        </form>

        {/* Quiet Single-Line Control Caption */}
        <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[11.5px] text-[var(--ink-muted)]">
          {run && (
            <span className="inline-flex items-center gap-1.5">
              <span
                className="w-1.5 h-1.5 rounded-full"
                style={{
                  backgroundColor: wsConnected ? "var(--accent)" : "var(--ink-muted)",
                }}
              />
              {run.phase.replace(/_/g, " ")}
            </span>
          )}

          {run && !isTerminal && (
            <>
              <span>·</span>
              {isPaused ? (
                <button
                  type="button"
                  onClick={() => void onResume()}
                  className="inline-flex items-center gap-1 hover:text-[var(--ink-primary)] transition-colors"
                  style={{ color: "var(--accent)" }}
                >
                  <Play className="w-3 h-3" />
                  Resume
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void onPause()}
                  className="inline-flex items-center gap-1 hover:text-[var(--ink-primary)] transition-colors"
                >
                  <Pause className="w-3 h-3" />
                  Pause
                </button>
              )}
              <button
                type="button"
                onClick={() => void onStop()}
                title="Stop run"
                aria-label="Stop run"
                className="inline-flex items-center gap-1 hover:text-[var(--status-danger)] transition-colors"
              >
                <Square className="w-2.5 h-2.5" />
                Stop
              </button>
            </>
          )}

          {autonomy && (
            <>
              <span>·</span>
              <select
                id="autonomy-select"
                aria-label="Autonomy preset"
                value={autonomy.preset}
                onChange={(e) =>
                  void onAutonomyChange(e.target.value as AutonomyConfig["preset"])
                }
                className="bg-transparent text-[11.5px] text-[var(--ink-secondary)] hover:text-[var(--ink-primary)] outline-none"
              >
                {AUTONOMY_PRESETS.map((p) => (
                  <option key={p} value={p} style={{ backgroundColor: "var(--bg-surface)" }}>
                    {p}
                  </option>
                ))}
              </select>
            </>
          )}

          <span>·</span>
          <button
            type="button"
            onClick={() => onToggleInspector("costs")}
            className="font-mono text-[11px] tabular hover:text-[var(--ink-primary)] transition-colors"
          >
            ${(costs?.spent_usd ?? run?.spent_usd ?? 0).toFixed(2)} / $
            {(costs?.budget_usd ?? run?.budget_usd ?? 5).toFixed(0)}
          </button>

          <span>·</span>
          <button
            type="button"
            onClick={() => onToggleInspector("outbox")}
            className="font-mono text-[11px] tabular hover:text-[var(--ink-primary)] transition-colors"
          >
            #{lastSeq}
          </button>
        </div>
      </div>
    </div>
  );
}
