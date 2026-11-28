import { useState } from "react";
import { Check, Lock, MessageSquarePlus, Terminal, X } from "lucide-react";
import type {
  BlackboardEvent,
  DecisionRecord,
  NoteRecord,
  ProjectSummary,
  RunRecord,
  TaskRecord,
  WriteReservationRecord,
} from "../types";
import { computeWaveProgress, formatShortTime, shortId } from "../utils/foundryMetrics";

interface ExecutionStreamProps {
  project: ProjectSummary | null;
  run: RunRecord | null;
  tasks: TaskRecord[];
  reservations: WriteReservationRecord[];
  decisions: DecisionRecord[];
  notes: NoteRecord[];
  events: BlackboardEvent[];
  demoMode: boolean;
  onOpenSettings: () => void;
  onResolveDecision: (
    decisionId: string,
    body: { action: "approve" | "reject"; choice?: unknown; feedback?: string }
  ) => Promise<void>;
  onOpenInspector: (section: "decisions" | "models" | "costs" | "outbox") => void;
  onOpenWorkspaceTasks: () => void;
}

function formatOption(opt: string): string {
  return opt.replace(/_/g, " ");
}

export function ExecutionStream({
  project,
  run,
  tasks,
  reservations,
  decisions,
  notes,
  events,
  demoMode,
  onOpenSettings,
  onResolveDecision,
  onOpenInspector,
  onOpenWorkspaceTasks,
}: ExecutionStreamProps) {
  const [selectedChoice, setSelectedChoice] = useState<Record<string, string>>({});
  const [feedbackById, setFeedbackById] = useState<Record<string, string>>({});
  const [showNote, setShowNote] = useState<Record<string, boolean>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  const activeLocksByTask = new Map<string, WriteReservationRecord[]>();
  for (const r of reservations) {
    if (r.released_at) continue;
    const list = activeLocksByTask.get(r.task_id) ?? [];
    list.push(r);
    activeLocksByTask.set(r.task_id, list);
  }

  const waveProgress = computeWaveProgress(tasks);
  const wave1 = waveProgress.find((w) => w.wave === 1);
  const wave2 = waveProgress.find((w) => w.wave === 2);
  const highlightedTasks = tasks.filter((t) =>
    ["IN_PROGRESS", "IN_REVIEW", "VERIFIED", "QUEUED", "ESCALATED"].includes(
      t.status.toUpperCase()
    )
  );
  const pendingDecisions = decisions.filter((d) => d.status === "pending");
  const activeReservations = reservations.filter((r) => !r.released_at);
  const projectLabel = run?.project_id ?? project?.name ?? "workspace";

  const getActiveChoice = (d: DecisionRecord): string | undefined => {
    if (selectedChoice[d.id]) return selectedChoice[d.id];
    if (typeof d.recommendation === "string" && d.options.includes(d.recommendation)) {
      return d.recommendation;
    }
    return d.options[0];
  };

  const handleDecision = async (d: DecisionRecord, action: "approve" | "reject") => {
    setBusyId(d.id);
    try {
      const choice = action === "approve" ? getActiveChoice(d) : "reject";
      const feedback = feedbackById[d.id]?.trim();
      await onResolveDecision(d.id, {
        action,
        choice,
        feedback: feedback ? feedback : undefined,
      });
    } finally {
      setBusyId(null);
    }
  };

  if (!run) {
    return (
      <div className="flex-1 min-h-0 overflow-y-auto px-6 sm:px-10 py-14 flex items-center justify-center">
        <div className="max-w-[520px] text-center space-y-4">
          <div
            className="w-11 h-11 mx-auto rounded-2xl border flex items-center justify-center"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--border-hairline)",
              color: "var(--ink-muted)",
            }}
          >
            <Terminal className="w-5 h-5" />
          </div>
          <h1 className="font-serif text-[26px] font-medium text-[var(--ink-primary)]">
            No active Foundry runs yet
          </h1>
          <p className="font-serif text-[17px] leading-relaxed text-[var(--ink-secondary)]">
            Connected cleanly to your local Foundry daemon for{" "}
            <span className="font-semibold text-[var(--ink-primary)]">{projectLabel}</span>. Start
            a run via the CLI or toggle sample data in Settings to inspect the full control plane.
          </p>
          <div className="pt-2 flex flex-wrap items-center justify-center gap-2.5">
            <code
              className="px-3.5 py-2 rounded-xl border font-mono text-[12px]"
              style={{
                backgroundColor: "var(--bg-surface)",
                borderColor: "var(--border-hairline)",
                color: "var(--ink-primary)",
              }}
            >
              foundry run --goal "..."
            </code>
            <button
              type="button"
              onClick={onOpenSettings}
              className="px-3.5 py-2 rounded-xl border text-[12.5px] font-medium hover:bg-[var(--bg-hover)] transition-colors"
              style={{
                backgroundColor: "var(--bg-surface)",
                borderColor: "var(--border-hairline)",
                color: "var(--accent)",
              }}
            >
              Open Settings
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-6 sm:px-10 py-8">
      <div className="max-w-[660px] mx-auto space-y-7 pb-8">
        {demoMode && (
          <div
            className="rounded-xl border px-3.5 py-2 flex items-center justify-between gap-3 text-[12px]"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--border-hairline)",
              color: "var(--ink-secondary)",
            }}
          >
            <span>Viewing interactive sample run snapshot.</span>
            <button
              type="button"
              onClick={onOpenSettings}
              className="font-medium hover:underline shrink-0"
              style={{ color: "var(--accent)" }}
            >
              Configure in Settings
            </button>
          </div>
        )}

        {/* Dynamic Editorial Run Summary (Newsreader Voice) */}
        <div className="space-y-3">
          {wave1 ? (
            <p className="font-serif text-[20px] leading-[1.55] text-[var(--ink-primary)]">
              {wave1.status === "merged" ? (
                <>
                  Wave 1 Foundation for{" "}
                  <span className="font-semibold">{projectLabel}</span> is complete — all{" "}
                  {wave1.merged} core tasks passed G1–G5 verification and merged cleanly to main.
                </>
              ) : (
                <>
                  Wave 1 Foundation for{" "}
                  <span className="font-semibold">{projectLabel}</span> is{" "}
                  {wave1.status} ({wave1.merged}/{wave1.total} merged) on run{" "}
                  <span className="font-mono text-[14px]">{shortId(run.id, 12)}</span>.
                </>
              )}
            </p>
          ) : (
            <p className="font-serif text-[20px] leading-[1.55] text-[var(--ink-primary)]">
              Run <span className="font-mono text-[15px]">{shortId(run.id, 12)}</span> for{" "}
              <span className="font-semibold">{projectLabel}</span> is currently in{" "}
              <span className="font-semibold">{run.phase.replace(/_/g, " ")}</span>.
            </p>
          )}

          {wave2 ? (
            <p className="font-serif text-[18.5px] leading-[1.6] text-[var(--ink-secondary)]">
              Wave 2 feature execution is {wave2.status} ({wave2.merged}/{wave2.total} merged,{" "}
              {wave2.inFlight} in flight) with independent cross-family G3/G4 verification.
            </p>
          ) : tasks.length === 0 ? (
            <p className="font-serif text-[18px] leading-[1.6] text-[var(--ink-secondary)]">
              Waiting for the orchestrator and domain leads to register Wave 1 foundation tasks and
              locked contracts on the Blackboard.
            </p>
          ) : null}
        </div>

        {/* Active Task Digest */}
        {(highlightedTasks.length > 0 || tasks.length > 0) && (
          <div className="space-y-3.5">
            {highlightedTasks.length > 0 && (
              <>
                <p className="font-serif text-[19px] leading-[1.55] text-[var(--ink-primary)]">
                  Here is what agents are actively working on:
                </p>

                <ul className="space-y-3 pl-5 list-disc marker:text-[var(--ink-muted)]">
                  {highlightedTasks.slice(0, 5).map((t) => {
                    const locks = activeLocksByTask.get(t.id) ?? [];
                    return (
                      <li
                        key={t.id}
                        className="font-serif text-[17.5px] leading-[1.55] text-[var(--ink-primary)]"
                      >
                        <span className="font-semibold">{t.title}</span>
                        <span className="text-[var(--ink-secondary)]">
                          {" — "}
                          {t.status.replace(/_/g, " ").toLowerCase()} (W{t.wave} {t.domain}
                          {locks.length > 0
                            ? `, locked ${locks[0].path.replace(/\/$/, "").split("/").pop()}`
                            : ""}
                          ).
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </>
            )}

            <div className="pt-1 text-center">
              <button
                type="button"
                onClick={onOpenWorkspaceTasks}
                className="text-[13.5px] font-medium hover:underline transition-colors"
                style={{ color: "var(--accent)" }}
              >
                View all {tasks.length} tasks
              </button>
            </div>
          </div>
        )}

        {/* Inline Pending Decision Cards */}
        {pendingDecisions.length > 0 && (
          <div className="space-y-4">
            <p className="font-serif text-[19px] leading-[1.55] text-[var(--ink-primary)]">
              {pendingDecisions.length === 1
                ? "One decision needs your sign-off before agents continue:"
                : `${pendingDecisions.length} decisions need your sign-off before agents continue:`}
            </p>

            <div className="space-y-3">
              {pendingDecisions.map((d) => {
                const isSecurity = d.category === "security" || d.category === "release_infra";
                const confPct = d.confidence != null ? Math.round(d.confidence * 100) : null;
                const activeChoice = getActiveChoice(d);
                const noteOpen = Boolean(showNote[d.id]);
                const isBusy = busyId === d.id;

                return (
                  <article
                    key={d.id}
                    className="rounded-2xl border p-4 sm:p-5 space-y-3.5"
                    style={{
                      backgroundColor: "var(--bg-surface)",
                      borderColor: isSecurity
                        ? "var(--status-danger-border)"
                        : "var(--border-hairline)",
                    }}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="flex items-center gap-2 text-[12px]">
                          <span
                            className="font-medium capitalize"
                            style={{
                              color: isSecurity ? "var(--status-danger)" : "var(--accent)",
                            }}
                          >
                            {d.category.replace(/_/g, " ")}
                          </span>
                          {confPct !== null && (
                            <span className="text-[var(--ink-muted)] tabular">
                              · {confPct}% confidence
                            </span>
                          )}
                        </div>
                        <h3 className="mt-1 text-[15px] font-semibold text-[var(--ink-primary)]">
                          {d.title}
                        </h3>
                      </div>
                      {d.reversibility && (
                        <span className="text-[11.5px] text-[var(--ink-muted)] shrink-0">
                          {d.reversibility} reversibility
                        </span>
                      )}
                    </div>

                    <p className="text-[13.5px] leading-relaxed text-[var(--ink-secondary)]">
                      {d.context}
                    </p>

                    {d.options.length > 0 && (
                      <div className="flex flex-wrap gap-2">
                        {d.options.map((opt) => {
                          const optStr = typeof opt === "string" ? opt : String(opt);
                          const active = activeChoice === optStr;
                          const isRec = d.recommendation === opt;
                          return (
                            <button
                              key={optStr}
                              type="button"
                              onClick={() =>
                                setSelectedChoice((prev) => ({ ...prev, [d.id]: optStr }))
                              }
                              aria-pressed={active}
                              className="px-3 py-1.5 rounded-full border text-[12.5px] transition-colors"
                              style={
                                active
                                  ? {
                                      backgroundColor: "var(--bg-active)",
                                      borderColor: "var(--border-strong)",
                                      color: "var(--ink-primary)",
                                      fontWeight: 500,
                                    }
                                  : {
                                      backgroundColor: "transparent",
                                      borderColor: "var(--border-hairline)",
                                      color: "var(--ink-secondary)",
                                    }
                              }
                            >
                              {formatOption(optStr)}
                              {isRec && (
                                <span
                                  className="ml-1.5 text-[11px]"
                                  style={{ color: "var(--accent)" }}
                                >
                                  · rec
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    )}

                    {noteOpen && (
                      <input
                        type="text"
                        value={feedbackById[d.id] ?? ""}
                        onChange={(e) =>
                          setFeedbackById((prev) => ({ ...prev, [d.id]: e.target.value }))
                        }
                        placeholder="Optional guidance for the agents…"
                        aria-label={`Optional guidance for ${d.title}`}
                        className="w-full h-9 px-3.5 rounded-xl border text-[13px] placeholder:text-[var(--ink-muted)]"
                        style={{
                          backgroundColor: "var(--bg-canvas)",
                          borderColor: "var(--border-hairline)",
                          color: "var(--ink-primary)",
                        }}
                      />
                    )}

                    <div className="flex items-center gap-2 pt-0.5">
                      <button
                        type="button"
                        disabled={isBusy}
                        onClick={() => handleDecision(d, "approve")}
                        className="h-9 px-4 rounded-full inline-flex items-center gap-1.5 text-[13px] font-medium transition-opacity hover:opacity-90 disabled:opacity-50"
                        style={{
                          backgroundColor: "var(--accent)",
                          color: "#ffffff",
                        }}
                      >
                        <Check className="w-3.5 h-3.5" />
                        Approve
                      </button>
                      <button
                        type="button"
                        disabled={isBusy}
                        onClick={() => handleDecision(d, "reject")}
                        className="h-9 px-3.5 rounded-full border inline-flex items-center gap-1 text-[13px] font-medium transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-50"
                        style={{
                          borderColor: "var(--border-hairline)",
                          color: "var(--ink-secondary)",
                        }}
                      >
                        <X className="w-3.5 h-3.5" />
                        Reject
                      </button>
                      {!noteOpen && (
                        <button
                          type="button"
                          onClick={() => setShowNote((prev) => ({ ...prev, [d.id]: true }))}
                          className="h-9 px-3 rounded-full inline-flex items-center gap-1.5 text-[12.5px] text-[var(--ink-muted)] hover:text-[var(--ink-primary)] hover:bg-[var(--bg-hover)]"
                        >
                          <MessageSquarePlus className="w-3.5 h-3.5" />
                          Add note
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>

            <div className="text-center">
              <button
                type="button"
                onClick={() => onOpenInspector("decisions")}
                className="text-[13.5px] font-medium hover:underline"
                style={{ color: "var(--accent)" }}
              >
                Open Decisions inbox
              </button>
            </div>
          </div>
        )}

        {/* Active Write Locks Compact Card */}
        {activeReservations.length > 0 && (
          <div
            className="rounded-2xl border px-4 py-3.5 flex items-center justify-between gap-3"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--border-hairline)",
            }}
          >
            <div className="flex items-center gap-2.5 min-w-0">
              <Lock className="w-4 h-4 shrink-0" style={{ color: "var(--accent)" }} />
              <div className="min-w-0">
                <div className="text-[13.5px] font-medium text-[var(--ink-primary)]">
                  {activeReservations.length} active write{" "}
                  {activeReservations.length === 1 ? "lock" : "locks"}
                </div>
                <div className="font-mono text-[11.5px] text-[var(--ink-muted)] truncate">
                  {activeReservations
                    .slice(0, 2)
                    .map((r) => r.path)
                    .join(" · ")}
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={() => onOpenInspector("outbox")}
              className="text-[12.5px] font-medium shrink-0 hover:underline"
              style={{ color: "var(--accent)" }}
            >
              Outbox ({events.length})
            </button>
          </div>
        )}

        {/* Persisted Blackboard Operator Notes (Warm Sand Bubbles) */}
        {notes.map((note) => {
          const isGotcha = note.kind === "gotcha";
          return (
            <div key={note.id} className="space-y-2">
              <div className="flex justify-end">
                <div
                  className="max-w-[440px] rounded-2xl px-4 py-3 font-serif text-[17px] leading-[1.5] text-[var(--ink-primary)]"
                  style={{ backgroundColor: "var(--bg-bubble)" }}
                >
                  {isGotcha && (
                    <span
                      className="block font-sans text-[11px] font-semibold uppercase tracking-wide mb-1"
                      style={{ color: "var(--status-warn)" }}
                    >
                      Gotcha
                    </span>
                  )}
                  {note.body}
                </div>
              </div>
              <p className="font-serif text-[16.5px] text-[var(--ink-secondary)]">
                Noted — persisted to run{" "}
                <span className="font-mono text-[13px]">{shortId(run.id, 12)}</span>
                {note.created_at ? ` at ${formatShortTime(note.created_at)}.` : "."}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
