import { Activity, Bell, Pause, Play, RefreshCw, Square } from "lucide-react";
import type { AutonomyConfig, MetricsSummaryResponse, ProjectSummary, RunRecord } from "../types";

interface TopBarProps {
  projects: ProjectSummary[];
  runs: RunRecord[];
  selectedRun: RunRecord | null;
  metrics: MetricsSummaryResponse | null;
  autonomy: AutonomyConfig | null;
  wsConnected: boolean;
  lastSeq: number;
  pendingDecisionCount: number;
  onSelectRun: (runId: string) => void;
  onControl: (action: "pause" | "resume" | "stop") => void;
  onChangeAutonomy: (preset: AutonomyConfig["preset"]) => void;
  onRefresh: () => void;
}

export function TopBar({
  projects,
  runs,
  selectedRun,
  metrics,
  autonomy,
  wsConnected,
  lastSeq,
  pendingDecisionCount,
  onSelectRun,
  onControl,
  onChangeAutonomy,
  onRefresh,
}: TopBarProps) {
  const merged = metrics?.tasks_by_status["MERGED"] ?? 0;
  const total = metrics?.tasks_total ?? 0;
  const pct = total > 0 ? Math.round((merged / total) * 100) : 0;

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-panelBorder bg-panel px-4 py-2.5 text-sm">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1.5 font-semibold tracking-tight text-white">
          <span className="h-2.5 w-2.5 rounded-full bg-indigo-500" />
          Foundry <span className="text-xs font-normal text-slate-400">v0.5</span>
        </span>
        <span className="text-slate-600">|</span>
        <span className="text-xs text-slate-400">
          {projects[0]?.name ?? "workspace"} ({projects[0]?.stack_pack ?? "fastapi-react-postgres"})
        </span>
        <select
          className="rounded border border-slate-700 bg-obsidian px-2 py-1 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
          value={selectedRun?.id ?? ""}
          onChange={(e) => onSelectRun(e.target.value)}
        >
          {runs.length === 0 ? (
            <option value="">No active runs</option>
          ) : (
            runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.id} ({r.phase})
              </option>
            ))
          )}
        </select>
      </div>

      <div className="flex items-center gap-4">
        {selectedRun && (
          <div className="flex items-center gap-2 text-xs">
            <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-indigo-300">
              {selectedRun.phase}
            </span>
            <div className="flex items-center gap-1.5">
              <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-800">
                <div className="h-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
              </div>
              <span className="text-slate-400">
                {merged}/{total} merged
              </span>
            </div>
            <span className="font-mono text-amber-300">
              ${selectedRun.spent_usd.toFixed(4)} / ${selectedRun.budget_usd.toFixed(2)}
            </span>
          </div>
        )}

        {autonomy && (
          <label className="flex items-center gap-1.5 text-xs text-slate-400">
            Autonomy
            <select
              className="rounded border border-slate-700 bg-obsidian px-2 py-1 text-xs text-slate-200"
              value={autonomy.preset}
              onChange={(e) => onChangeAutonomy(e.target.value as AutonomyConfig["preset"])}
            >
              <option value="autopilot">autopilot</option>
              <option value="balanced">balanced</option>
              <option value="architect">architect</option>
              <option value="full_control">full_control</option>
              <option value="benchmark">benchmark</option>
            </select>
          </label>
        )}

        {selectedRun && (
          <div className="flex items-center gap-1">
            <button
              onClick={() => onControl("pause")}
              title="Pause run"
              className="rounded border border-slate-700 p-1.5 text-slate-300 hover:bg-slate-800"
            >
              <Pause className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => onControl("resume")}
              title="Resume run"
              className="rounded border border-slate-700 p-1.5 text-emerald-400 hover:bg-slate-800"
            >
              <Play className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => onControl("stop")}
              title="Stop run"
              className="rounded border border-slate-700 p-1.5 text-rose-400 hover:bg-slate-800"
            >
              <Square className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        <div className="flex items-center gap-2 text-xs">
          <span
            className={`flex items-center gap-1 rounded-full px-2 py-0.5 ${
              wsConnected
                ? "bg-emerald-950/70 text-emerald-300 border border-emerald-800"
                : "bg-slate-800 text-slate-400"
            }`}
          >
            <Activity className="h-3 w-3" />
            {wsConnected ? `seq #${lastSeq}` : "offline"}
          </span>
          <span className="flex items-center gap-1 rounded-full bg-slate-800 px-2 py-0.5 text-amber-300">
            <Bell className="h-3 w-3" />
            {pendingDecisionCount}
          </span>
          <button
            onClick={onRefresh}
            title="Refresh"
            className="rounded border border-slate-700 p-1.5 text-slate-300 hover:bg-slate-800"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </header>
  );
}
