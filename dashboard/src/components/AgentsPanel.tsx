import type { AgentRecord } from "../types";

export function AgentsPanel({ agents }: { agents: AgentRecord[] }) {
  return (
    <div className="space-y-2">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
        Agents ({agents.length})
      </h2>
      {agents.length === 0 ? (
        <p className="rounded border border-dashed border-slate-800 p-4 text-xs text-slate-500">
          No registered agent threads for this run yet.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {agents.map((a) => (
            <div key={a.id} className="rounded border border-panelBorder bg-obsidian/70 p-3 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-white">{a.role}</span>
                <span className="rounded bg-slate-800 px-2 py-0.5 text-[11px] text-indigo-300">
                  {a.status}
                </span>
              </div>
              <div className="mt-1 text-slate-400">
                Domain: <span className="text-slate-200">{a.domain ?? "global"}</span> · Profile:{" "}
                <span className="font-mono text-emerald-300">{a.model_profile}</span>
              </div>
              {a.graph_thread_id && (
                <div className="mt-1 truncate font-mono text-[11px] text-slate-500">
                  thread: {a.graph_thread_id}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
