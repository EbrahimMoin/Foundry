import { useState } from "react";
import { ArrowRight, CheckCircle2, Circle, Lock } from "lucide-react";
import { AgentsPanel } from "./AgentsPanel";
import { ContractsPanel } from "./ContractsPanel";
import { PreviewPanel } from "./PreviewPanel";
import { TasksPanel } from "./TasksPanel";
import type {
  AgentRecord,
  ContractRecord,
  DecisionRecord,
  ModelsConfig,
  TaskRecord,
  WriteReservationRecord,
} from "../types";

export type WorkspaceTab = "overview" | "tasks" | "contracts" | "agents" | "preview";

interface WorkspaceHubProps {
  initialTab?: WorkspaceTab;
  runId: string | null;
  tasks: TaskRecord[];
  reservations: WriteReservationRecord[];
  contracts: ContractRecord[];
  agents: AgentRecord[];
  decisions: DecisionRecord[];
  models: ModelsConfig | null;
  onOpenDecisions: () => void;
  onAnnounce: (msg: string) => void;
}

export function WorkspaceHub({
  initialTab = "overview",
  runId,
  tasks,
  reservations,
  contracts,
  agents,
  decisions,
  models,
  onOpenDecisions,
  onAnnounce,
}: WorkspaceHubProps) {
  const [tab, setTab] = useState<WorkspaceTab>(initialTab);

  const pendingDecisions = decisions.filter((d) => d.status === "pending");
  const wave2Tasks = tasks.filter((t) => t.wave === 2);
  const previewTasks = wave2Tasks.length > 0 ? wave2Tasks : tasks;

  const pills: Array<{ key: WorkspaceTab; label: string }> = [
    { key: "overview", label: "Top" },
    { key: "tasks", label: `Tasks (${tasks.length})` },
    { key: "contracts", label: `Contracts (${contracts.length})` },
    { key: "agents", label: `Agents (${agents.length})` },
    { key: "preview", label: "Preview" },
  ];

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="shrink-0 px-6 sm:px-10 pt-8 pb-4">
        <div className={tab === "overview" ? "max-w-[660px] mx-auto" : "max-w-[1160px] mx-auto"}>
          <h1 className="font-serif text-[32px] font-medium tracking-tight text-[var(--ink-primary)]">
            Workspace
          </h1>

          <div
            className="mt-4 flex flex-wrap items-center gap-2"
            role="tablist"
            aria-label="Workspace sections"
          >
            {pills.map((p) => {
              const active = tab === p.key;
              return (
                <button
                  key={p.key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setTab(p.key)}
                  className="h-9 px-4 rounded-full border text-[13.5px] transition-colors"
                  style={
                    active
                      ? {
                          backgroundColor: "var(--bg-active)",
                          borderColor: "transparent",
                          color: "var(--ink-primary)",
                          fontWeight: 500,
                        }
                      : {
                          backgroundColor: "var(--bg-surface)",
                          borderColor: "var(--border-hairline)",
                          color: "var(--ink-secondary)",
                        }
                  }
                >
                  {p.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {tab === "overview" ? (
        <div className="flex-1 min-h-0 overflow-y-auto px-6 sm:px-10 pb-10">
          <div className="max-w-[660px] mx-auto space-y-5">
            <section
              className="rounded-2xl border p-5 space-y-3.5"
              style={{
                backgroundColor: "var(--bg-surface)",
                borderColor: "var(--border-hairline)",
              }}
            >
              <div className="flex items-center justify-between gap-2">
                <h2 className="font-serif text-[21px] font-medium text-[var(--ink-primary)]">
                  Pending decisions
                </h2>
                <button
                  type="button"
                  onClick={onOpenDecisions}
                  aria-label="Open all decisions"
                  className="h-8 w-8 rounded-full flex items-center justify-center text-[var(--ink-secondary)] hover:bg-[var(--bg-hover)]"
                >
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>

              {pendingDecisions.length === 0 ? (
                <p className="text-[13.5px] text-[var(--ink-muted)]">
                  All caught up — no pending decisions.
                </p>
              ) : (
                <div className="space-y-2">
                  {pendingDecisions.map((d) => (
                    <button
                      key={d.id}
                      type="button"
                      onClick={onOpenDecisions}
                      className="w-full text-left rounded-xl border px-4 py-3 transition-colors hover:bg-[var(--bg-hover)]"
                      style={{
                        borderColor: "var(--border-hairline)",
                        backgroundColor: "var(--bg-canvas)",
                      }}
                    >
                      <div className="text-[14px] font-medium text-[var(--ink-primary)]">
                        {d.title}
                      </div>
                      <div className="mt-0.5 text-[12px] text-[var(--ink-muted)] capitalize">
                        {d.category.replace(/_/g, " ")}
                        {d.confidence != null
                          ? ` · ${Math.round(d.confidence * 100)}% confidence`
                          : ""}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </section>

            <section
              className="rounded-2xl border p-5 space-y-3.5"
              style={{
                backgroundColor: "var(--bg-surface)",
                borderColor: "var(--border-hairline)",
              }}
            >
              <div className="flex items-center justify-between gap-2">
                <h2 className="font-serif text-[21px] font-medium text-[var(--ink-primary)]">
                  {wave2Tasks.length > 0 ? "Wave 2 tasks" : "Tasks"}
                </h2>
                <button
                  type="button"
                  onClick={() => setTab("tasks")}
                  aria-label="Open tasks board"
                  className="h-8 w-8 rounded-full flex items-center justify-center text-[var(--ink-secondary)] hover:bg-[var(--bg-hover)]"
                >
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>

              {previewTasks.length === 0 ? (
                <p className="text-[13.5px] text-[var(--ink-muted)]">No tasks registered yet.</p>
              ) : (
                <div
                  className="rounded-xl border divide-y overflow-hidden"
                  style={{
                    borderColor: "var(--border-hairline)",
                    backgroundColor: "var(--bg-canvas)",
                  }}
                >
                  {previewTasks.slice(0, 4).map((t) => {
                    const done = t.status === "MERGED" || t.status === "VERIFIED";
                    const locked = reservations.some((r) => r.task_id === t.id && !r.released_at);
                    return (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => setTab("tasks")}
                        className="w-full text-left px-4 py-3 flex items-center justify-between gap-3 hover:bg-[var(--bg-hover)] transition-colors"
                        style={{ borderColor: "var(--border-hairline)" }}
                      >
                        <div className="flex items-center gap-3 min-w-0">
                          {done ? (
                            <CheckCircle2
                              className="w-4 h-4 shrink-0"
                              style={{ color: "var(--accent)" }}
                            />
                          ) : (
                            <Circle className="w-4 h-4 shrink-0 text-[var(--ink-muted)]" />
                          )}
                          <span className="text-[13.5px] font-medium text-[var(--ink-primary)] truncate">
                            {t.title}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 shrink-0 text-[12px] text-[var(--ink-muted)]">
                          {locked && (
                            <Lock className="w-3.5 h-3.5" style={{ color: "var(--status-warn)" }} />
                          )}
                          <span className="capitalize">
                            {t.status.replace(/_/g, " ").toLowerCase()}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}

              {tasks.length > 4 && (
                <div className="text-center pt-1">
                  <button
                    type="button"
                    onClick={() => setTab("tasks")}
                    className="text-[13px] font-medium hover:underline"
                    style={{ color: "var(--accent)" }}
                  >
                    {tasks.length - 4} more
                  </button>
                </div>
              )}
            </section>

            <section
              className="rounded-2xl border p-5 space-y-3"
              style={{
                backgroundColor: "var(--bg-surface)",
                borderColor: "var(--border-hairline)",
              }}
            >
              <div className="flex items-center justify-between gap-2">
                <h2 className="font-serif text-[21px] font-medium text-[var(--ink-primary)]">
                  Locked contracts
                </h2>
                <button
                  type="button"
                  onClick={() => setTab("contracts")}
                  aria-label="Open all contracts"
                  className="h-8 w-8 rounded-full flex items-center justify-center text-[var(--ink-secondary)] hover:bg-[var(--bg-hover)]"
                >
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>

              {contracts.length === 0 ? (
                <p className="text-[13.5px] text-[var(--ink-muted)]">No contracts locked yet.</p>
              ) : (
                <div className="space-y-1.5 text-[13.5px]">
                  {contracts.slice(0, 4).map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => setTab("contracts")}
                      className="w-full text-left flex items-center justify-between gap-2 py-1 text-[var(--ink-secondary)] hover:text-[var(--ink-primary)]"
                    >
                      <span className="text-[var(--ink-primary)] font-medium truncate">
                        {c.name}
                      </span>
                      <span className="font-mono text-[11.5px] text-[var(--ink-muted)] shrink-0">
                        v{c.current_version} · {c.status}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </div>
        </div>
      ) : (
        <div className="flex-1 min-h-0 overflow-hidden px-3 sm:px-6 pb-4">
          <div className="max-w-[1160px] mx-auto h-full flex flex-col">
            {tab === "tasks" && (
              <TasksPanel
                tasks={tasks}
                reservations={reservations}
                onAnnounce={onAnnounce}
              />
            )}
            {tab === "contracts" && (
              <ContractsPanel contracts={contracts} onAnnounce={onAnnounce} />
            )}
            {tab === "agents" && (
              <AgentsPanel agents={agents} models={models} onAnnounce={onAnnounce} />
            )}
            {tab === "preview" && <PreviewPanel runId={runId} />}
          </div>
        </div>
      )}
    </div>
  );
}
