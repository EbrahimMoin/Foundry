import { useCallback, useEffect, useRef, useState } from "react";
import { api, RunEventStream } from "./api";
import { ActivityDrawer } from "./components/ActivityDrawer";
import { AgentsPanel } from "./components/AgentsPanel";
import { CostsMetricsPanel } from "./components/CostsMetricsPanel";
import { DecisionsPanel } from "./components/DecisionsPanel";
import { ModelSwitcherPanel } from "./components/ModelSwitcherPanel";
import { PreviewPanel } from "./components/PreviewPanel";
import { TasksPanel } from "./components/TasksPanel";
import { TopBar } from "./components/TopBar";
import type {
  AgentRecord,
  AutonomyConfig,
  BlackboardEvent,
  ContractRecord,
  CostsSummaryResponse,
  DataPolicyConfig,
  DecisionRecord,
  MetricsSummaryResponse,
  ModelsConfig,
  PreviewUrlResponse,
  ProjectSummary,
  RunRecord,
  TaskRecord,
  WriteReservationRecord,
} from "./types";

type CenterTab = "preview" | "tasks" | "agents" | "contracts";
type RightTab = "decisions" | "models" | "costs";

export function App() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [reservations, setReservations] = useState<WriteReservationRecord[]>([]);
  const [contracts, setContracts] = useState<ContractRecord[]>([]);
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [costs, setCosts] = useState<CostsSummaryResponse | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummaryResponse | null>(null);
  const [models, setModels] = useState<ModelsConfig | null>(null);
  const [autonomy, setAutonomy] = useState<AutonomyConfig | null>(null);
  const [dataPolicy, setDataPolicy] = useState<DataPolicyConfig | null>(null);
  const [previewEnv, setPreviewEnv] = useState<"local" | "staging" | "production">("local");
  const [preview, setPreview] = useState<PreviewUrlResponse | null>(null);
  const [events, setEvents] = useState<BlackboardEvent[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const [lastSeq, setLastSeq] = useState(0);
  const [centerTab, setCenterTab] = useState<CenterTab>("tasks");
  const [rightTab, setRightTab] = useState<RightTab>("decisions");
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const streamRef = useRef<RunEventStream | null>(null);

  const loadGlobal = useCallback(async () => {
    try {
      const [projList, runList, modelCfg, autCfg, dpCfg] = await Promise.all([
        api.listProjects(),
        api.listRuns(),
        api.getModels(),
        api.getAutonomy(),
        api.getDataPolicy(),
      ]);
      setProjects(projList);
      setRuns(runList);
      setModels(modelCfg);
      setAutonomy(autCfg);
      setDataPolicy(dpCfg);
      setSelectedRunId((cur) => cur || runList[0]?.id || "");
    } catch (err) {
      setErrorBanner(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadRunData = useCallback(
    async (runId: string) => {
      if (!runId) return;
      try {
        const [tList, aList, rList, cList, dList, costSum, metSum, prev] = await Promise.all([
          api.listTasks(runId),
          api.listAgents(runId),
          api.listReservations(runId),
          api.listContracts(runId),
          api.listDecisions(runId),
          api.getCosts(runId),
          api.getMetrics(runId),
          api.getPreviewUrl(runId, previewEnv),
        ]);
        setTasks(tList);
        setAgents(aList);
        setReservations(rList);
        setContracts(cList);
        setDecisions(dList);
        setCosts(costSum);
        setMetrics(metSum);
        setPreview(prev);
        setErrorBanner(null);
      } catch (err) {
        setErrorBanner(err instanceof Error ? err.message : String(err));
      }
    },
    [previewEnv]
  );

  useEffect(() => {
    void loadGlobal();
  }, [loadGlobal]);

  useEffect(() => {
    if (!selectedRunId) return;
    void loadRunData(selectedRunId);
  }, [selectedRunId, loadRunData]);

  useEffect(() => {
    streamRef.current?.stop();
    setEvents([]);
    setLastSeq(0);
    if (!selectedRunId) return;

    const stream = new RunEventStream(
      selectedRunId,
      (ev) => {
        setEvents((prev) => [...prev.slice(-199), ev]);
        void loadRunData(selectedRunId);
      },
      (connected, seq) => {
        setWsConnected(connected);
        setLastSeq(seq);
      }
    );
    streamRef.current = stream;
    stream.start();
    return () => stream.stop();
  }, [selectedRunId, loadRunData]);

  const selectedRun = runs.find((r) => r.id === selectedRunId) ?? null;
  const pendingDecisions = decisions.filter((d) => d.status === "pending");

  const handleControl = async (action: "pause" | "resume" | "stop") => {
    if (!selectedRunId) return;
    if (action === "pause") await api.pauseRun(selectedRunId);
    if (action === "resume") await api.resumeRun(selectedRunId);
    if (action === "stop") await api.stopRun(selectedRunId);
    await loadGlobal();
    await loadRunData(selectedRunId);
  };

  const handleResolveDecision = async (
    decisionId: string,
    action: "approve" | "reject",
    choice?: unknown
  ) => {
    await api.resolveDecision(decisionId, { action, choice });
    if (selectedRunId) await loadRunData(selectedRunId);
  };

  const handleAssignRole = async (role: string, profile: string) => {
    const updated = await api.updateModelAssignments({ [role]: profile });
    setModels(updated);
  };

  const handleTogglePolicy = async (policy: DataPolicyConfig) => {
    const updated = await api.updateDataPolicy(policy);
    setDataPolicy(updated);
  };

  const handleChangeAutonomy = async (preset: AutonomyConfig["preset"]) => {
    const updated = await api.updateAutonomy(preset);
    setAutonomy(updated);
  };

  return (
    <div className="flex min-h-screen flex-col bg-obsidian text-slate-100">
      <TopBar
        projects={projects}
        runs={runs}
        selectedRun={selectedRun}
        metrics={metrics}
        autonomy={autonomy}
        wsConnected={wsConnected}
        lastSeq={lastSeq}
        pendingDecisionCount={pendingDecisions.length}
        onSelectRun={setSelectedRunId}
        onControl={(a) => void handleControl(a)}
        onChangeAutonomy={(p) => void handleChangeAutonomy(p)}
        onRefresh={() => {
          void loadGlobal();
          if (selectedRunId) void loadRunData(selectedRunId);
        }}
      />

      {errorBanner && (
        <div className="border-b border-rose-800 bg-rose-950/60 px-4 py-1.5 text-xs text-rose-200">
          {errorBanner}
        </div>
      )}

      <div className="grid flex-1 grid-cols-1 lg:grid-cols-12">
        <main className="flex flex-col border-b border-panelBorder p-4 lg:col-span-8 lg:border-b-0 lg:border-r">
          <div className="mb-3 flex gap-2 border-b border-panelBorder pb-2 text-xs">
            {(["tasks", "preview", "agents", "contracts"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setCenterTab(tab)}
                className={`rounded px-3 py-1 font-medium capitalize ${
                  centerTab === tab
                    ? "bg-indigo-600 text-white"
                    : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                }`}
              >
                {tab}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-auto">
            {centerTab === "tasks" && <TasksPanel tasks={tasks} reservations={reservations} />}
            {centerTab === "preview" && (
              <PreviewPanel preview={preview} env={previewEnv} onChangeEnv={setPreviewEnv} />
            )}
            {centerTab === "agents" && <AgentsPanel agents={agents} />}
            {centerTab === "contracts" && (
              <div className="space-y-2 text-xs">
                <h2 className="font-semibold uppercase tracking-wider text-slate-400">
                  Locked Contracts ({contracts.length})
                </h2>
                {contracts.map((c) => (
                  <div
                    key={c.id}
                    className="flex items-center justify-between rounded border border-panelBorder bg-panel px-3 py-2"
                  >
                    <div>
                      <span className="font-semibold text-white">{c.name}</span>{" "}
                      <span className="text-slate-400">
                        ({c.domain} · {c.kind})
                      </span>
                    </div>
                    <span className="font-mono text-emerald-300">
                      v{c.current_version} · {c.status}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </main>

        <aside className="flex flex-col bg-panel/40 p-4 lg:col-span-4">
          <div className="mb-3 flex gap-2 border-b border-panelBorder pb-2 text-xs">
            {(["decisions", "models", "costs"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setRightTab(tab)}
                className={`rounded px-3 py-1 font-medium capitalize ${
                  rightTab === tab
                    ? "bg-indigo-600 text-white"
                    : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                }`}
              >
                {tab}
                {tab === "decisions" && pendingDecisions.length > 0
                  ? ` (${pendingDecisions.length})`
                  : ""}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-auto">
            {rightTab === "decisions" && (
              <DecisionsPanel
                decisions={decisions}
                onResolve={(id, action, choice) => void handleResolveDecision(id, action, choice)}
              />
            )}
            {rightTab === "models" && (
              <ModelSwitcherPanel
                models={models}
                dataPolicy={dataPolicy}
                onAssignRole={(r, p) => void handleAssignRole(r, p)}
                onToggleDataPolicy={(dp) => void handleTogglePolicy(dp)}
              />
            )}
            {rightTab === "costs" && <CostsMetricsPanel costs={costs} metrics={metrics} />}
          </div>
        </aside>
      </div>

      <ActivityDrawer events={events} />
    </div>
  );
}
