import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  Copy,
  FileCode2,
  GitBranch,
  LayoutGrid,
  List,
  Lock,
  Search,
  X,
} from "lucide-react";
import { api } from "../api";
import type { TaskDetailResponse, TaskRecord, WriteReservationRecord } from "../types";
import { deriveTaskGates, filterTasks, type GateState } from "../utils/foundryMetrics";

interface TasksPanelProps {
  tasks: TaskRecord[];
  reservations: WriteReservationRecord[];
  onAnnounce?: (msg: string) => void;
}

const BOARD_COLUMNS = [
  { key: "PENDING", label: "Pending", dot: "var(--ink-muted)" },
  { key: "READY", label: "Ready", dot: "var(--ink-muted)" },
  { key: "IN_PROGRESS", label: "In Progress", dot: "var(--status-active)" },
  { key: "IN_REVIEW", label: "In Review", dot: "var(--status-warn)" },
  { key: "VERIFIED", label: "Verified", dot: "var(--status-violet)" },
  { key: "QUEUED", label: "Queued", dot: "var(--accent)" },
  { key: "MERGED", label: "Merged", dot: "var(--status-nominal)" },
  { key: "ESCALATED", label: "Escalated", dot: "var(--status-danger)" },
  { key: "FAILED", label: "Failed", dot: "var(--status-danger)" },
] as const;

function gateDotColor(state: GateState): string {
  switch (state) {
    case "passed":
      return "var(--status-nominal)";
    case "active":
      return "var(--status-active)";
    case "failed":
      return "var(--status-danger)";
    default:
      return "var(--border-strong)";
  }
}

function statusColor(status: string): string {
  switch (status.toUpperCase()) {
    case "MERGED":
      return "var(--status-nominal)";
    case "IN_PROGRESS":
      return "var(--status-active)";
    case "IN_REVIEW":
      return "var(--status-warn)";
    case "VERIFIED":
      return "var(--status-violet)";
    case "QUEUED":
      return "var(--accent)";
    case "ESCALATED":
    case "FAILED":
      return "var(--status-danger)";
    default:
      return "var(--ink-muted)";
  }
}

export function TasksPanel({ tasks, reservations, onAnnounce }: TasksPanelProps) {
  const [view, setView] = useState<"board" | "list">("board");
  const [waveFilter, setWaveFilter] = useState<number | "all">("all");
  const [query, setQuery] = useState("");
  const [selectedTask, setSelectedTask] = useState<TaskRecord | null>(null);
  const [detail, setDetail] = useState<TaskDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const dialogRef = useRef<HTMLDialogElement | null>(null);

  const activeLocksByTask = useMemo(() => {
    const map = new Map<string, WriteReservationRecord[]>();
    for (const r of reservations) {
      if (r.released_at) continue;
      const list = map.get(r.task_id) ?? [];
      list.push(r);
      map.set(r.task_id, list);
    }
    return map;
  }, [reservations]);

  const filteredTasks = useMemo(
    () => filterTasks(tasks, { wave: waveFilter, query }),
    [tasks, waveFilter, query]
  );

  const visibleColumns = useMemo(() => {
    const activeStatuses = new Set(filteredTasks.map((t) => t.status.toUpperCase()));
    return BOARD_COLUMNS.filter((c) => activeStatuses.has(c.key));
  }, [filteredTasks]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (selectedTask) {
      if (!dialog.open) dialog.showModal();
    } else if (dialog.open) {
      dialog.close();
    }
  }, [selectedTask]);

  // Mandatory Safari light-dismiss fallback for browsers lacking <dialog closedby>
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if ("closedBy" in HTMLDialogElement.prototype) return;

    const handleBackdropClick = (event: MouseEvent) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      const inside =
        rect.top <= event.clientY &&
        event.clientY <= rect.top + rect.height &&
        rect.left <= event.clientX &&
        event.clientX <= rect.left + rect.width;
      if (!inside) dialog.close();
    };
    dialog.addEventListener("click", handleBackdropClick);
    return () => dialog.removeEventListener("click", handleBackdropClick);
  }, []);

  useEffect(() => {
    if (!selectedTask) {
      setDetail(null);
      return;
    }
    let active = true;
    setDetailLoading(true);
    api
      .getTaskDetail(selectedTask.id)
      .then((res) => {
        if (active) setDetail(res);
      })
      .catch(() => {
        if (active) setDetail(null);
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedTask]);

  const copyText = (text: string, label: string) => {
    navigator.clipboard?.writeText(text).catch(() => {});
    onAnnounce?.(`Copied ${label}`);
  };

  const selectedTaskLocks =
    detail?.reservations ?? (selectedTask ? activeLocksByTask.get(selectedTask.id) ?? [] : []);

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="shrink-0 px-5 pt-4 pb-2 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search
              className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
              style={{ color: "var(--ink-muted)" }}
            />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search tasks…"
              aria-label="Filter tasks"
              className="h-8 pl-8 pr-3 rounded-[6px] border text-[12.5px] w-48 sm:w-56 placeholder:text-[var(--ink-muted)]"
              style={{
                backgroundColor: "var(--bg-surface)",
                borderColor: "var(--border-hairline)",
                color: "var(--ink-primary)",
              }}
            />
          </div>

          <div className="flex items-center gap-1 text-[12px]" role="group" aria-label="Filter by wave">
            {(["all", 1, 2] as const).map((w) => {
              const active = waveFilter === w;
              return (
                <button
                  key={String(w)}
                  type="button"
                  onClick={() => setWaveFilter(w)}
                  className="px-2.5 py-1 rounded-[6px] font-medium transition-colors"
                  style={
                    active
                      ? { backgroundColor: "var(--bg-active)", color: "var(--ink-primary)" }
                      : { color: "var(--ink-muted)" }
                  }
                >
                  {w === "all" ? "All" : `Wave ${w}`}
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex items-center gap-1 text-[12px]" role="group" aria-label="Task view mode">
          <button
            type="button"
            onClick={() => setView("board")}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-[6px] font-medium transition-colors"
            style={
              view === "board"
                ? { backgroundColor: "var(--bg-active)", color: "var(--ink-primary)" }
                : { color: "var(--ink-muted)" }
            }
          >
            <LayoutGrid className="w-3.5 h-3.5" />
            Board
          </button>
          <button
            type="button"
            onClick={() => setView("list")}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-[6px] font-medium transition-colors"
            style={
              view === "list"
                ? { backgroundColor: "var(--bg-active)", color: "var(--ink-primary)" }
                : { color: "var(--ink-muted)" }
            }
          >
            <List className="w-3.5 h-3.5" />
            List
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-auto px-5 py-3">
        {filteredTasks.length === 0 ? (
          <div className="py-16 text-center">
            <p className="text-[13.5px] font-medium text-[var(--ink-primary)]">No tasks found</p>
            <p className="text-[12.5px] text-[var(--ink-muted)] mt-1">
              Try clearing your search or switching wave filter.
            </p>
            {(query || waveFilter !== "all") && (
              <button
                type="button"
                onClick={() => {
                  setQuery("");
                  setWaveFilter("all");
                }}
                className="mt-3 px-3 py-1.5 rounded-[6px] text-[12px] font-medium"
                style={{
                  backgroundColor: "var(--bg-active)",
                  color: "var(--ink-primary)",
                }}
              >
                Reset filters
              </button>
            )}
          </div>
        ) : view === "board" ? (
          <div className="flex items-start gap-5 h-full min-h-[320px] pb-3">
            {visibleColumns.map((col) => {
              const items = filteredTasks.filter((t) => t.status.toUpperCase() === col.key);
              return (
                <section
                  key={col.key}
                  aria-label={`${col.label} tasks`}
                  className="w-[248px] shrink-0 flex flex-col max-h-full"
                >
                  <div className="flex items-center justify-between px-1 pb-2.5">
                    <div className="flex items-center gap-2">
                      <span
                        className="w-2 h-2 rounded-full"
                        style={{ backgroundColor: col.dot }}
                        aria-hidden="true"
                      />
                      <h3 className="text-[12.5px] font-semibold text-[var(--ink-primary)]">
                        {col.label}
                      </h3>
                    </div>
                    <span className="font-mono text-[11.5px] text-[var(--ink-muted)] tabular">
                      {items.length}
                    </span>
                  </div>

                  <div className="space-y-2.5 overflow-y-auto pr-0.5">
                    {items.map((task) => {
                      const gates = deriveTaskGates(task);
                      const locks = activeLocksByTask.get(task.id) ?? [];
                      const totalSpend = task.spent_implement_usd + task.spent_review_usd;
                      return (
                        <button
                          key={task.id}
                          type="button"
                          onClick={() => setSelectedTask(task)}
                          className="w-full text-left rounded-[8px] border p-3.5 transition-colors hover:border-[var(--border-strong)]"
                          style={{
                            backgroundColor: "var(--bg-surface)",
                            borderColor: "var(--border-hairline)",
                          }}
                        >
                          <div className="flex items-center justify-between gap-2 text-[11px] text-[var(--ink-muted)]">
                            <span>
                              W{task.wave} · {task.domain}
                            </span>
                            {totalSpend > 0 && (
                              <span className="font-mono tabular">${totalSpend.toFixed(3)}</span>
                            )}
                          </div>

                          <div className="mt-1.5 text-[13px] font-medium leading-snug text-[var(--ink-primary)]">
                            {task.title}
                          </div>

                          <div className="mt-3 flex items-center justify-between gap-2">
                            <div
                              className="inline-flex items-center gap-1"
                              title={gates.map((g) => `${g.id}: ${g.state}`).join(" · ")}
                              aria-label="Gate progress G1 through G5"
                            >
                              {gates.map((g) => (
                                <span
                                  key={g.id}
                                  className="w-2 h-1 rounded-full"
                                  style={{ backgroundColor: gateDotColor(g.state) }}
                                />
                              ))}
                            </div>

                            {locks.length > 0 && (
                              <span
                                className="inline-flex items-center gap-1 font-mono text-[10.5px] truncate max-w-[155px]"
                                style={{ color: "var(--status-warn)" }}
                                title={locks.map((l) => l.path).join("\n")}
                              >
                                <Lock className="w-3 h-3 shrink-0" />
                                <span className="truncate">
                                  {locks[0].path.replace(/\/$/, "").split("/").pop()}
                                  {locks.length > 1 ? ` +${locks.length - 1}` : ""}
                                </span>
                              </span>
                            )}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </section>
              );
            })}
          </div>
        ) : (
          <div
            className="rounded-[8px] border overflow-x-auto"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--border-hairline)",
            }}
          >
            <table className="w-full text-left border-collapse min-w-[620px]">
              <tbody className="divide-y" style={{ borderColor: "var(--border-hairline)" }}>
                {filteredTasks.map((task) => {
                  const gates = deriveTaskGates(task);
                  const locks = activeLocksByTask.get(task.id) ?? [];
                  const totalSpend = task.spent_implement_usd + task.spent_review_usd;
                  return (
                    <tr
                      key={task.id}
                      onClick={() => setSelectedTask(task)}
                      className="cursor-pointer transition-colors hover:bg-[var(--bg-hover)]"
                      style={{ borderColor: "var(--border-hairline)" }}
                    >
                      <td className="py-2.5 px-3.5 w-28 whitespace-nowrap">
                        <span
                          className="inline-flex items-center gap-1.5 text-[11.5px] font-medium"
                          style={{ color: statusColor(task.status) }}
                        >
                          <span
                            className="w-1.5 h-1.5 rounded-full"
                            style={{ backgroundColor: statusColor(task.status) }}
                          />
                          {task.status.replace(/_/g, " ").toLowerCase()}
                        </span>
                      </td>
                      <td className="py-2.5 px-3">
                        <div className="text-[13px] font-medium text-[var(--ink-primary)]">
                          {task.title}
                        </div>
                      </td>
                      <td className="py-2.5 px-3 text-[11.5px] text-[var(--ink-muted)] whitespace-nowrap">
                        W{task.wave} · {task.domain}
                      </td>
                      <td className="py-2.5 px-3 whitespace-nowrap">
                        <div
                          className="inline-flex items-center gap-1"
                          title={gates.map((g) => `${g.id}: ${g.state}`).join(" · ")}
                        >
                          {gates.map((g) => (
                            <span
                              key={g.id}
                              className="w-2.5 h-1 rounded-full"
                              style={{ backgroundColor: gateDotColor(g.state) }}
                            />
                          ))}
                        </div>
                      </td>
                      <td className="py-2.5 px-3 font-mono text-[11px] whitespace-nowrap">
                        {locks.length > 0 ? (
                          <span
                            className="inline-flex items-center gap-1"
                            style={{ color: "var(--status-warn)" }}
                          >
                            <Lock className="w-3 h-3" />
                            {locks.length}
                          </span>
                        ) : (
                          <span className="text-[var(--ink-muted)]">—</span>
                        )}
                      </td>
                      <td className="py-2.5 px-3.5 font-mono text-[11.5px] text-right tabular text-[var(--ink-secondary)]">
                        ${totalSpend.toFixed(3)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Native <dialog closedby="any"> Task Inspector */}
      <dialog
        ref={dialogRef}
        closedby="any"
        className="foundry-dialog"
        aria-labelledby="task-inspector-title"
        onClose={() => setSelectedTask(null)}
      >
        {selectedTask && (
          <div className="flex flex-col max-h-[calc(100vh-3rem)]">
            <div
              className="px-6 py-4 border-b flex items-start justify-between gap-4 shrink-0"
              style={{ borderColor: "var(--border-hairline)" }}
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-[12px] text-[var(--ink-muted)]">
                  <span style={{ color: statusColor(selectedTask.status) }} className="font-medium">
                    {selectedTask.status.replace(/_/g, " ")}
                  </span>
                  <span>·</span>
                  <span>Wave {selectedTask.wave}</span>
                  <span>·</span>
                  <span>{selectedTask.domain}</span>
                  {selectedTask.feature && (
                    <>
                      <span>·</span>
                      <span className="font-mono">{selectedTask.feature}</span>
                    </>
                  )}
                </div>
                <h2
                  id="task-inspector-title"
                  className="mt-1 text-[16px] font-semibold text-[var(--ink-primary)]"
                >
                  {selectedTask.title}
                </h2>
              </div>
              <button
                type="button"
                onClick={() => setSelectedTask(null)}
                aria-label="Close task inspector"
                className="h-8 w-8 rounded-full flex items-center justify-center hover:bg-[var(--bg-hover)] text-[var(--ink-muted)]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-6 overflow-y-auto space-y-5 text-[13px]">
              <p className="text-[var(--ink-secondary)] leading-relaxed">
                {selectedTask.description}
              </p>

              <div className="grid grid-cols-5 gap-2">
                {deriveTaskGates(selectedTask).map((g) => (
                  <div
                    key={g.id}
                    className="rounded-[6px] border px-2.5 py-2"
                    style={{
                      backgroundColor: "var(--bg-elevated)",
                      borderColor: "var(--border-hairline)",
                    }}
                  >
                    <div className="flex items-center gap-1.5 font-mono text-[11px] font-semibold text-[var(--ink-primary)]">
                      <span
                        className="w-1.5 h-1.5 rounded-full"
                        style={{ backgroundColor: gateDotColor(g.state) }}
                      />
                      {g.id}
                    </div>
                    <div className="text-[10.5px] text-[var(--ink-muted)] truncate mt-0.5">
                      {g.label}
                    </div>
                  </div>
                ))}
              </div>

              {selectedTask.acceptance.length > 0 && (
                <div>
                  <h3 className="text-[11.5px] font-semibold text-[var(--ink-muted)] mb-2">
                    Acceptance Criteria
                  </h3>
                  <ul className="space-y-2">
                    {selectedTask.acceptance.map((item, i) => (
                      <li key={i} className="flex items-start gap-2.5 text-[var(--ink-primary)]">
                        <CheckCircle2
                          className="w-4 h-4 mt-0.5 shrink-0"
                          style={{
                            color:
                              selectedTask.status === "MERGED" || selectedTask.status === "VERIFIED"
                                ? "var(--status-nominal)"
                                : "var(--ink-muted)",
                          }}
                        />
                        <span className="leading-snug">
                          {typeof item === "string" ? item : JSON.stringify(item)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {detailLoading ? (
                <div className="text-[12px] text-[var(--ink-muted)]">
                  Loading write set & dependencies…
                </div>
              ) : (
                detail &&
                (detail.write_set.length > 0 || detail.dependencies.length > 0) && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {detail.write_set.length > 0 && (
                      <div>
                        <h3 className="text-[11.5px] font-semibold text-[var(--ink-muted)] mb-1.5">
                          Write Set
                        </h3>
                        <div className="space-y-1 font-mono text-[11px]">
                          {detail.write_set.map((path) => (
                            <div
                              key={path}
                              className="flex items-center gap-1.5 px-2.5 py-1 rounded border truncate"
                              style={{
                                backgroundColor: "var(--bg-canvas)",
                                borderColor: "var(--border-hairline)",
                                color: "var(--ink-primary)",
                              }}
                            >
                              <FileCode2 className="w-3 h-3 shrink-0 text-[var(--ink-muted)]" />
                              <span className="truncate">{path}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {detail.dependencies.length > 0 && (
                      <div>
                        <h3 className="text-[11.5px] font-semibold text-[var(--ink-muted)] mb-1.5">
                          Blocked By
                        </h3>
                        <div className="space-y-1 font-mono text-[11px]">
                          {detail.dependencies.map((dep) => (
                            <div
                              key={dep}
                              className="px-2.5 py-1 rounded border truncate"
                              style={{
                                backgroundColor: "var(--bg-canvas)",
                                borderColor: "var(--border-hairline)",
                                color: "var(--ink-secondary)",
                              }}
                            >
                              {dep}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )
              )}

              {selectedTaskLocks.length > 0 && (
                <div>
                  <h3 className="text-[11.5px] font-semibold text-[var(--ink-muted)] mb-2">
                    Active Write Locks
                  </h3>
                  <div className="space-y-1.5 font-mono text-[11.5px]">
                    {selectedTaskLocks.map((lock) => (
                      <div
                        key={lock.id}
                        className="flex items-center gap-2 px-3 py-1.5 rounded-[6px]"
                        style={{
                          backgroundColor: "var(--status-warn-bg)",
                          color: "var(--status-warn)",
                        }}
                      >
                        <Lock className="w-3.5 h-3.5 shrink-0" />
                        <span className="truncate">{lock.path}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div
                className="pt-4 border-t grid grid-cols-2 gap-3 font-mono text-[11px] text-[var(--ink-secondary)]"
                style={{ borderColor: "var(--border-hairline)" }}
              >
                <div className="flex items-center gap-1.5 truncate">
                  <GitBranch className="w-3.5 h-3.5 text-[var(--ink-muted)] shrink-0" />
                  <span className="truncate">{selectedTask.branch ?? "no branch"}</span>
                </div>
                <div className="text-right tabular">
                  Try {selectedTask.attempt}/{selectedTask.max_attempts} · $
                  {(selectedTask.spent_implement_usd + selectedTask.spent_review_usd).toFixed(4)}
                </div>
                {selectedTask.graph_thread_id && (
                  <button
                    type="button"
                    onClick={() => copyText(selectedTask.graph_thread_id!, "thread ID")}
                    className="col-span-2 inline-flex items-center gap-1.5 text-[var(--ink-muted)] hover:text-[var(--ink-primary)] truncate"
                  >
                    <Copy className="w-3 h-3 shrink-0" />
                    <span className="truncate">{selectedTask.graph_thread_id}</span>
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
      </dialog>
    </div>
  );
}
