import { useState } from "react";
import { Lock } from "lucide-react";
import type { TaskRecord, WriteReservationRecord } from "../types";

interface TasksPanelProps {
  tasks: TaskRecord[];
  reservations: WriteReservationRecord[];
}

const STATUS_BADGE: Record<string, string> = {
  PENDING: "bg-slate-800 text-slate-300",
  BLOCKED: "bg-slate-800 text-slate-400",
  READY: "bg-sky-950 text-sky-300 border border-sky-800",
  IN_PROGRESS: "bg-indigo-950 text-indigo-300 border border-indigo-800",
  IN_REVIEW: "bg-amber-950 text-amber-300 border border-amber-800",
  VERIFIED: "bg-teal-950 text-teal-300 border border-teal-800",
  QUEUED: "bg-purple-950 text-purple-300 border border-purple-800",
  MERGED: "bg-emerald-950 text-emerald-300 border border-emerald-800",
  ESCALATED: "bg-rose-950 text-rose-300 border border-rose-800",
};

const COLUMNS = ["READY", "IN_PROGRESS", "IN_REVIEW", "VERIFIED", "QUEUED", "MERGED", "ESCALATED"];

export function TasksPanel({ tasks, reservations }: TasksPanelProps) {
  const [view, setView] = useState<"board" | "list">("board");
  const locksByTask = new Map<string, WriteReservationRecord[]>();
  for (const res of reservations) {
    const arr = locksByTask.get(res.task_id) ?? [];
    arr.push(res);
    locksByTask.set(res.task_id, arr);
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Tasks ({tasks.length})
        </h2>
        <div className="flex gap-1 rounded border border-slate-800 bg-obsidian p-0.5 text-xs">
          <button
            onClick={() => setView("board")}
            className={`rounded px-2 py-0.5 ${view === "board" ? "bg-indigo-600 text-white" : "text-slate-400"}`}
          >
            Board
          </button>
          <button
            onClick={() => setView("list")}
            className={`rounded px-2 py-0.5 ${view === "list" ? "bg-indigo-600 text-white" : "text-slate-400"}`}
          >
            List
          </button>
        </div>
      </div>

      {view === "board" ? (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3 lg:grid-cols-4">
          {COLUMNS.map((col) => {
            const items = tasks.filter((t) => t.status === col);
            return (
              <div key={col} className="rounded border border-panelBorder bg-obsidian/60 p-2">
                <div className="mb-2 flex items-center justify-between text-[11px] font-medium text-slate-400">
                  <span>{col}</span>
                  <span>{items.length}</span>
                </div>
                <div className="space-y-2">
                  {items.map((t) => {
                    const locks = locksByTask.get(t.id) ?? [];
                    return (
                      <div
                        key={t.id}
                        className="rounded border border-slate-800 bg-panel p-2 text-xs"
                      >
                        <div className="flex items-center justify-between text-[10px] text-slate-400">
                          <span>
                            W{t.wave} · {t.domain}/{t.kind}
                          </span>
                          <span>try {t.attempt}/{t.max_attempts}</span>
                        </div>
                        <div className="mt-1 font-medium text-slate-100">{t.title}</div>
                        {locks.length > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {locks.map((l) => (
                              <span
                                key={l.id}
                                className="inline-flex items-center gap-1 rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-amber-300"
                              >
                                <Lock className="h-2.5 w-2.5" />
                                {l.path}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="overflow-x-auto rounded border border-panelBorder">
          <table className="w-full text-left text-xs">
            <thead className="bg-obsidian text-slate-400">
              <tr>
                <th className="p-2">ID</th>
                <th className="p-2">Wave</th>
                <th className="p-2">Domain / Kind</th>
                <th className="p-2">Status</th>
                <th className="p-2">Try</th>
                <th className="p-2">Reopens</th>
                <th className="p-2">Title</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {tasks.map((t) => (
                <tr key={t.id} className="hover:bg-slate-900/60">
                  <td className="p-2 font-mono text-slate-400">{t.id}</td>
                  <td className="p-2">{t.wave}</td>
                  <td className="p-2">
                    {t.domain} / {t.kind}
                  </td>
                  <td className="p-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[11px] ${
                        STATUS_BADGE[t.status] ?? "bg-slate-800 text-slate-300"
                      }`}
                    >
                      {t.status}
                    </span>
                  </td>
                  <td className="p-2">
                    {t.attempt}/{t.max_attempts}
                  </td>
                  <td className="p-2">{t.contract_reopens}</td>
                  <td className="p-2 text-slate-100">{t.title}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
