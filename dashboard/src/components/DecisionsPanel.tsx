import { useState } from "react";
import { Check, CheckCircle2, MessageSquarePlus, X } from "lucide-react";
import type { DecisionRecord } from "../types";

interface DecisionsPanelProps {
  decisions: DecisionRecord[];
  onResolve: (
    decisionId: string,
    body: { action: "approve" | "reject"; choice?: unknown; feedback?: string }
  ) => Promise<void>;
}

function formatOption(opt: string): string {
  return opt.replace(/_/g, " ");
}

export function DecisionsPanel({ decisions, onResolve }: DecisionsPanelProps) {
  const [selectedChoice, setSelectedChoice] = useState<Record<string, string>>({});
  const [feedbackById, setFeedbackById] = useState<Record<string, string>>({});
  const [showNote, setShowNote] = useState<Record<string, boolean>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  const pending = decisions.filter((d) => d.status === "pending");
  const resolved = decisions.filter((d) => d.status !== "pending");

  const getActiveChoice = (d: DecisionRecord): string | undefined => {
    if (selectedChoice[d.id]) return selectedChoice[d.id];
    if (typeof d.recommendation === "string" && d.options.includes(d.recommendation)) {
      return d.recommendation;
    }
    return d.options[0];
  };

  const handleAction = async (d: DecisionRecord, action: "approve" | "reject") => {
    setBusyId(d.id);
    try {
      const choice = action === "approve" ? getActiveChoice(d) : "reject";
      const feedback = feedbackById[d.id]?.trim();
      await onResolve(d.id, {
        action,
        choice,
        feedback: feedback ? feedback : undefined,
      });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="p-4 space-y-6 overflow-y-auto h-full">
      <section aria-label="Pending human decisions" className="space-y-3">
        {pending.length === 0 ? (
          <div className="py-10 text-center">
            <CheckCircle2
              className="w-5 h-5 mx-auto mb-2"
              style={{ color: "var(--status-nominal)" }}
            />
            <p className="text-[13px] font-medium text-[var(--ink-primary)]">All caught up</p>
            <p className="text-[12px] text-[var(--ink-muted)] mt-0.5">
              No pending human decisions.
            </p>
          </div>
        ) : (
          pending.map((d) => {
            const isSecurity = d.category === "security" || d.category === "release_infra";
            const confidencePct = d.confidence != null ? Math.round(d.confidence * 100) : null;
            const activeChoice = getActiveChoice(d);
            const isBusy = busyId === d.id;
            const noteOpen = Boolean(showNote[d.id]);

            return (
              <article
                key={d.id}
                className="rounded-[8px] border p-4 space-y-3"
                style={{
                  backgroundColor: "var(--bg-surface)",
                  borderColor: isSecurity
                    ? "var(--status-danger-border)"
                    : "var(--border-hairline)",
                }}
              >
                <div className="flex items-center justify-between gap-2 text-[11px]">
                  <span
                    className="font-medium capitalize"
                    style={{
                      color: isSecurity ? "var(--status-danger)" : "var(--accent-strong)",
                    }}
                  >
                    {d.category.replace(/_/g, " ")}
                  </span>
                  <span className="font-mono text-[var(--ink-muted)] tabular">
                    {confidencePct !== null ? `${confidencePct}% conf` : d.mode}
                    {d.reversibility ? ` · ${d.reversibility} rev` : ""}
                  </span>
                </div>

                <div>
                  <h3 className="text-[13.5px] font-semibold leading-snug text-[var(--ink-primary)]">
                    {d.title}
                  </h3>
                  <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--ink-secondary)]">
                    {d.context}
                  </p>
                </div>

                {d.options.length > 0 && (
                  <div className="space-y-1">
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
                          className="w-full text-left px-2.5 py-1.5 rounded-[6px] flex items-center justify-between gap-2 text-[12px] transition-colors"
                          style={
                            active
                              ? {
                                  backgroundColor: "var(--accent-soft)",
                                  color: "var(--ink-primary)",
                                  fontWeight: 500,
                                }
                              : {
                                  backgroundColor: "transparent",
                                  color: "var(--ink-secondary)",
                                }
                          }
                        >
                          <span className="truncate">{formatOption(optStr)}</span>
                          {isRec && (
                            <span
                              className="text-[10.5px] shrink-0"
                              style={{ color: "var(--accent-strong)" }}
                            >
                              Recommended
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}

                {noteOpen && (
                  <input
                    id={`feedback-${d.id}`}
                    type="text"
                    value={feedbackById[d.id] ?? ""}
                    onChange={(e) =>
                      setFeedbackById((prev) => ({ ...prev, [d.id]: e.target.value }))
                    }
                    placeholder="Add feedback note…"
                    aria-label={`Optional feedback note for ${d.title}`}
                    className="w-full h-8 px-2.5 rounded-[6px] border text-[12px] placeholder:text-[var(--ink-muted)]"
                    style={{
                      backgroundColor: "var(--bg-elevated)",
                      borderColor: "var(--border-hairline)",
                      color: "var(--ink-primary)",
                    }}
                  />
                )}

                <div className="flex items-center gap-2 pt-1">
                  <button
                    type="button"
                    disabled={isBusy}
                    onClick={() => handleAction(d, "approve")}
                    className="flex-1 h-8 rounded-[6px] inline-flex items-center justify-center gap-1.5 text-[12px] font-semibold transition-colors disabled:opacity-50"
                    style={{
                      backgroundColor: "var(--status-nominal-bg)",
                      color: "var(--status-nominal)",
                    }}
                  >
                    <Check className="w-3.5 h-3.5" />
                    Approve
                  </button>
                  <button
                    type="button"
                    disabled={isBusy}
                    onClick={() => handleAction(d, "reject")}
                    className="h-8 px-3 rounded-[6px] inline-flex items-center justify-center gap-1 text-[12px] font-medium transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-50"
                    style={{ color: "var(--status-danger)" }}
                  >
                    <X className="w-3.5 h-3.5" />
                    Reject
                  </button>
                  {!noteOpen && (
                    <button
                      type="button"
                      onClick={() => setShowNote((prev) => ({ ...prev, [d.id]: true }))}
                      title="Add feedback note"
                      aria-label="Add feedback note"
                      className="h-8 w-8 rounded-[6px] flex items-center justify-center hover:bg-[var(--bg-hover)] text-[var(--ink-muted)]"
                    >
                      <MessageSquarePlus className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </article>
            );
          })
        )}
      </section>

      {resolved.length > 0 && (
        <section aria-label="Resolved Architecture Decision Records">
          <h2 className="text-[11.5px] font-medium text-[var(--ink-muted)] mb-2.5">
            Resolved ({resolved.length})
          </h2>
          <div className="space-y-2">
            {resolved.map((d) => {
              const rejected = d.answer?.action === "reject";
              return (
                <div
                  key={d.id}
                  className="px-3 py-2.5 rounded-[7px] border"
                  style={{
                    backgroundColor: "var(--bg-surface)",
                    borderColor: "var(--border-hairline)",
                  }}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-[12.5px] text-[var(--ink-primary)] leading-snug">
                      {d.title}
                    </span>
                    <span
                      className="text-[11px] shrink-0 font-medium"
                      style={{
                        color: rejected ? "var(--status-danger)" : "var(--status-nominal)",
                      }}
                    >
                      {rejected ? "Rejected" : "Approved"}
                    </span>
                  </div>
                  {d.answer?.feedback && (
                    <p className="mt-1 text-[11.5px] text-[var(--ink-muted)]">
                      “{d.answer.feedback}”
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
