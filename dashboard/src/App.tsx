import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, MoreHorizontal } from "lucide-react";
import {
  api,
  isDaemonOnline,
  isDemoMode,
  probeDaemonHealth,
  RunEventStream,
  setManualDemoMode,
  subscribeConnectionMode,
} from "./api";
import { CommandDock, type InspectorSection } from "./components/CommandDock";
import { ExecutionStream } from "./components/ExecutionStream";
import { InspectorDrawer } from "./components/InspectorDrawer";
import { SettingsModal } from "./components/SettingsModal";
import { Sidebar, type PrimarySection } from "./components/Sidebar";
import { TelemetryHub } from "./components/TelemetryHub";
import { WorkspaceHub, type WorkspaceTab } from "./components/WorkspaceHub";
import { useTheme } from "./hooks/useTheme";
import type {
  AgentRecord,
  AutonomyConfig,
  AutonomyOverrides,
  BlackboardEvent,
  ContractRecord,
  CostsSummaryResponse,
  DataPolicyConfig,
  DecisionRecord,
  MetricsSummaryResponse,
  ModelsConfig,
  NoteRecord,
  ProjectSummary,
  RunRecord,
  RuntimeSettingsResponse,
  TaskRecord,
  WriteReservationRecord,
} from "./types";

export function App() {
  const {
    preference: themePreference,
    systemTheme,
    theme,
    setPreference: setThemePreference,
    toggleTheme,
  } = useTheme();

  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [reservations, setReservations] = useState<WriteReservationRecord[]>([]);
  const [contracts, setContracts] = useState<ContractRecord[]>([]);
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [notes, setNotes] = useState<NoteRecord[]>([]);
  const [costs, setCosts] = useState<CostsSummaryResponse | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummaryResponse | null>(null);
  const [models, setModels] = useState<ModelsConfig | null>(null);
  const [autonomy, setAutonomy] = useState<AutonomyConfig | null>(null);
  const [dataPolicy, setDataPolicy] = useState<DataPolicyConfig | null>(null);
  const [runtimeSettings, setRuntimeSettings] = useState<RuntimeSettingsResponse | null>(null);
  const [events, setEvents] = useState<BlackboardEvent[]>([]);

  const [wsConnected, setWsConnected] = useState(false);
  const [lastSeq, setLastSeq] = useState(0);
  const [daemonOnline, setDaemonOnline] = useState(isDaemonOnline());
  const [demoMode, setDemoMode] = useState(isDemoMode());

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [section, setSection] = useState<PrimarySection>("stream");
  const [workspaceInitialTab, setWorkspaceInitialTab] = useState<WorkspaceTab>("overview");
  const [workspaceKey, setWorkspaceKey] = useState(0);
  const [inspector, setInspector] = useState<InspectorSection | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  const streamRef = useRef<RunEventStream | null>(null);

  const announce = useCallback((message: string) => {
    setAnnouncement("");
    window.requestAnimationFrame(() => setAnnouncement(message));
  }, []);

  const loadGlobal = useCallback(async () => {
    try {
      await probeDaemonHealth();
      const [projs, runList, modCfg, autoCfg, policyCfg, rtCfg] = await Promise.all([
        api.listProjects(),
        api.listRuns(),
        api.getModels(),
        api.getAutonomy(),
        api.getDataPolicy(),
        api.getRuntimeSettings(),
      ]);
      setProjects(projs);
      setRuns(runList);
      setModels(modCfg);
      setAutonomy(autoCfg);
      setDataPolicy(policyCfg);
      setRuntimeSettings(rtCfg);
      setSelectedRunId((prev) =>
        prev && runList.some((r) => r.id === prev) ? prev : (runList[0]?.id ?? null)
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadRunData = useCallback(async (runId: string) => {
    try {
      const [ag, tk, resv, ctr, dec, runNotes, cst, met] = await Promise.all([
        api.listAgents(runId),
        api.listTasks(runId),
        api.listReservations(runId),
        api.listContracts(runId),
        api.listDecisions(runId),
        api.listNotes(runId),
        api.getCosts(runId),
        api.getMetrics(runId),
      ]);
      setAgents(ag);
      setTasks(tk);
      setReservations(resv);
      setContracts(ctr);
      setDecisions(dec);
      setNotes(runNotes);
      setCosts(cst);
      setMetrics(met);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    const unsub = subscribeConnectionMode((next) => {
      setDaemonOnline(next.daemonOnline);
      setDemoMode(next.demoMode);
    });
    void loadGlobal();
    return unsub;
  }, [loadGlobal]);

  // Global keyboard shortcut: Ctrl+, / Cmd+, toggles centered Settings modal
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === ",") {
        e.preventDefault();
        setSettingsOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!selectedRunId) {
      setAgents([]);
      setTasks([]);
      setReservations([]);
      setContracts([]);
      setDecisions([]);
      setNotes([]);
      setCosts(null);
      setMetrics(null);
      setEvents([]);
      setLastSeq(0);
      setWsConnected(false);
      return;
    }

    void loadRunData(selectedRunId);
    setEvents([]);
    setLastSeq(0);

    streamRef.current?.stop();
    const stream = new RunEventStream(
      selectedRunId,
      (ev) => {
        setEvents((prev) => {
          const exists = prev.some(
            (p) => (p.publish_seq ?? p.id) === (ev.publish_seq ?? ev.id) && p.type === ev.type
          );
          if (exists) return prev;
          return [...prev.slice(-249), ev];
        });
        const seq = ev.publish_seq ?? ev.id;
        setLastSeq((prev) => (seq > prev ? seq : prev));

        if (
          ev.type.startsWith("run.") ||
          ev.type.startsWith("task.") ||
          ev.type.startsWith("decision.") ||
          ev.type.startsWith("contract.") ||
          ev.type.startsWith("main.")
        ) {
          void loadRunData(selectedRunId);
        }
      },
      setWsConnected
    );
    stream.start();
    streamRef.current = stream;

    return () => {
      stream.stop();
    };
  }, [selectedRunId, demoMode, loadRunData]);

  const refreshAll = useCallback(async () => {
    await loadGlobal();
    if (selectedRunId) await loadRunData(selectedRunId);
  }, [loadGlobal, loadRunData, selectedRunId]);

  const handlePause = async () => {
    if (!selectedRunId) return;
    await api.pauseRun(selectedRunId);
    await refreshAll();
    announce("Run paused");
  };

  const handleResume = async () => {
    if (!selectedRunId) return;
    await api.resumeRun(selectedRunId);
    await refreshAll();
    announce("Run resumed");
  };

  const handleStop = async () => {
    if (!selectedRunId) return;
    await api.stopRun(selectedRunId);
    await refreshAll();
    announce("Run stopped");
  };

  const handleAutonomyPatch = async (patch: {
    preset?: AutonomyConfig["preset"];
    overrides?: AutonomyOverrides;
    confidence_threshold?: number;
  }) => {
    try {
      const updated = await api.updateAutonomy(patch);
      setAutonomy(updated);
      announce("Autonomy updated");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleResolveDecision = async (
    decisionId: string,
    body: { action: "approve" | "reject"; choice?: unknown; feedback?: string }
  ) => {
    try {
      await api.resolveDecision(decisionId, body);
      await refreshAll();
      announce(`Decision ${body.action === "approve" ? "approved" : "rejected"}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleUpdateRole = async (role: string, profile: string) => {
    try {
      const updated = await api.updateModelAssignments({ [role]: profile });
      setModels(updated);
      announce(`Assigned ${role} to ${profile}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleUpdateDataPolicy = async (patch: Partial<DataPolicyConfig>) => {
    try {
      const updated = await api.updateDataPolicy(patch);
      setDataPolicy(updated);
      announce("Updated data policy");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleToggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    toggleTheme();
    announce(`Switched to ${next} mode`);
  };

  const handleToggleDemoMode = async (enabled: boolean) => {
    setManualDemoMode(enabled);
    setSelectedRunId(null);
    await loadGlobal();
    announce(enabled ? "Demo preview enabled" : "Switched to live daemon");
  };

  const handleReconnectDaemon = async (): Promise<boolean> => {
    const ok = await probeDaemonHealth();
    if (ok) {
      setManualDemoMode(false);
    }
    await loadGlobal();
    return ok;
  };

  const openInspector = (target: InspectorSection) => {
    setInspector(target);
    setSidebarCollapsed(true);
  };

  const toggleInspector = (target: InspectorSection) => {
    if (inspector === target) {
      setInspector(null);
    } else {
      openInspector(target);
    }
  };

  const handlePostNote = async (text: string, kind: "note" | "gotcha" = "note") => {
    if (!selectedRunId) return;
    try {
      const created = await api.createNote(selectedRunId, text, kind);
      setNotes((prev) =>
        prev.some((n) => n.id === created.id) ? prev : [...prev, created]
      );
      setSection("stream");
      announce("Note persisted to Blackboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const activeRun = runs.find((r) => r.id === selectedRunId) ?? null;
  const activeProject =
    projects.find((p) => p.id === activeRun?.project_id) ?? projects[0] ?? null;
  const pendingDecisionsCount = decisions.filter((d) => d.status === "pending").length;

  return (
    <div
      className="h-screen flex overflow-hidden"
      style={{ backgroundColor: "var(--bg-canvas)", color: "var(--ink-primary)" }}
    >
      <div role="status" aria-live="polite" className="sr-only">
        {announcement}
      </div>

      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((c) => !c)}
        activeSection={section}
        onSelectSection={(next) => {
          setSection(next);
          if (next === "workspace") {
            setWorkspaceInitialTab("overview");
            setWorkspaceKey((k) => k + 1);
          }
        }}
        runs={runs}
        selectedRunId={selectedRunId}
        onSelectRun={setSelectedRunId}
        pendingDecisions={pendingDecisionsCount}
        theme={theme}
        onToggleTheme={handleToggleTheme}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <main className="flex-1 min-w-0 flex flex-col h-full overflow-hidden relative">
        <div className="absolute top-3.5 right-4 z-10 flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => toggleInspector("decisions")}
            aria-label="Open run details and decisions"
            title="Decisions, models, costs & outbox"
            className="h-8 w-8 rounded-full flex items-center justify-center text-[var(--ink-secondary)] hover:bg-[var(--bg-hover)] transition-colors"
          >
            <MoreHorizontal className="w-[18px] h-[18px]" />
          </button>
        </div>

        {error && (
          <div
            className="shrink-0 px-6 py-2.5 border-b flex items-center justify-between gap-3 text-[12.5px]"
            style={{
              backgroundColor: "var(--status-danger-bg)",
              borderColor: "var(--status-danger-border)",
              color: "var(--status-danger)",
            }}
            role="alert"
          >
            <div className="flex items-center gap-2 min-w-0">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span className="truncate">{error}</span>
            </div>
            <button
              type="button"
              onClick={() => setError(null)}
              className="text-[12px] underline shrink-0"
            >
              Dismiss
            </button>
          </div>
        )}

        {section === "stream" && (
          <>
            <ExecutionStream
              project={activeProject}
              run={activeRun}
              tasks={tasks}
              reservations={reservations}
              decisions={decisions}
              notes={notes}
              events={events}
              demoMode={demoMode}
              onOpenSettings={() => setSettingsOpen(true)}
              onResolveDecision={handleResolveDecision}
              onOpenInspector={openInspector}
              onOpenWorkspaceTasks={() => {
                setWorkspaceInitialTab("tasks");
                setWorkspaceKey((k) => k + 1);
                setSection("workspace");
              }}
            />

            <CommandDock
              run={activeRun}
              autonomy={autonomy}
              costs={costs}
              lastSeq={lastSeq}
              wsConnected={wsConnected}
              onToggleInspector={toggleInspector}
              onAutonomyChange={async (preset) => handleAutonomyPatch({ preset })}
              onPause={handlePause}
              onResume={handleResume}
              onStop={handleStop}
              onPostNote={handlePostNote}
            />
          </>
        )}

        {section === "workspace" && (
          <WorkspaceHub
            key={workspaceKey}
            initialTab={workspaceInitialTab}
            runId={selectedRunId}
            tasks={tasks}
            reservations={reservations}
            contracts={contracts}
            agents={agents}
            decisions={decisions}
            models={models}
            onOpenDecisions={() => openInspector("decisions")}
            onAnnounce={announce}
          />
        )}

        {section === "controls" && (
          <TelemetryHub
            models={models}
            dataPolicy={dataPolicy}
            costs={costs}
            metrics={metrics}
            onUpdateRole={handleUpdateRole}
            onUpdateDataPolicy={handleUpdateDataPolicy}
            onOpenFullSettings={() => setSettingsOpen(true)}
          />
        )}
      </main>

      {inspector && (
        <InspectorDrawer
          section={inspector}
          onChangeSection={setInspector}
          onClose={() => {
            setInspector(null);
            setSidebarCollapsed(false);
          }}
          decisions={decisions}
          onResolveDecision={handleResolveDecision}
          models={models}
          dataPolicy={dataPolicy}
          onUpdateRole={handleUpdateRole}
          onUpdateDataPolicy={handleUpdateDataPolicy}
          onOpenFullSettings={() => setSettingsOpen(true)}
          costs={costs}
          metrics={metrics}
          events={events}
        />
      )}

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        themePreference={themePreference}
        systemTheme={systemTheme}
        onChangeThemePreference={setThemePreference}
        sidebarCollapsed={sidebarCollapsed}
        onToggleSidebarCollapsed={() => setSidebarCollapsed((c) => !c)}
        daemonOnline={daemonOnline}
        demoMode={demoMode}
        onToggleDemoMode={(enabled) => void handleToggleDemoMode(enabled)}
        onReconnectDaemon={handleReconnectDaemon}
        autonomy={autonomy}
        onUpdateAutonomy={handleAutonomyPatch}
        models={models}
        onUpdateRole={handleUpdateRole}
        dataPolicy={dataPolicy}
        onUpdateDataPolicy={handleUpdateDataPolicy}
        runtimeSettings={runtimeSettings}
      />
    </div>
  );
}

export default App;
