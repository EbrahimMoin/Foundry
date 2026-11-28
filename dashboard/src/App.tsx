import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Bot, KeyRound, Layers, MoreHorizontal } from "lucide-react";
import {
  api,
  isDaemonOnline,
  isDemoMode,
  probeDaemonHealth,
  RunEventStream,
  setManualDemoMode,
  subscribeConnectionMode,
} from "./api";
import { AgentLayersStudio } from "./components/AgentLayersStudio";
import { CommandDock, type InspectorSection } from "./components/CommandDock";
import { ExecutionStream } from "./components/ExecutionStream";
import { InspectorDrawer } from "./components/InspectorDrawer";
import { NewChatView } from "./components/NewChatView";
import { SettingsModal, type SettingsTab } from "./components/SettingsModal";
import { Sidebar, type PrimarySection } from "./components/Sidebar";
import { TelemetryHub } from "./components/TelemetryHub";
import { WorkspaceHub, type WorkspaceTab } from "./components/WorkspaceHub";
import { useTheme } from "./hooks/useTheme";
import type {
  AgentAuthSource,
  AgentLayer,
  AgentRecord,
  AgentRole,
  AgentRosterConfig,
  ApiKeysStatusResponse,
  AutonomyConfig,
  AutonomyOverrides,
  BlackboardEvent,
  ChatSessionSummary,
  ContractRecord,
  CostsSummaryResponse,
  DataPolicyConfig,
  DecisionRecord,
  KeyModelsResponse,
  MessageRecord,
  MetricsSummaryResponse,
  ModelProfileUpsertPayload,
  ModelsConfig,
  NoteRecord,
  OpenAISubscriptionStatus,
  ProjectSummary,
  ProviderCatalogEntry,
  RunRecord,
  RuntimeSettingsResponse,
  TaskRecord,
  TestConnectionResponse,
  WriteReservationRecord,
} from "./types";

const TEST_PROJECT_PREFIXES = ["live_p5_", "pgsaver_", "outbox_", "p4_"];

function isTestProjectSlug(slug: string | undefined | null): boolean {
  if (!slug) return false;
  return TEST_PROJECT_PREFIXES.some((prefix) => slug.startsWith(prefix));
}

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
  const [chats, setChats] = useState<ChatSessionSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [reservations, setReservations] = useState<WriteReservationRecord[]>([]);
  const [contracts, setContracts] = useState<ContractRecord[]>([]);
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [messages, setMessages] = useState<MessageRecord[]>([]);
  const [notes, setNotes] = useState<NoteRecord[]>([]);
  const [costs, setCosts] = useState<CostsSummaryResponse | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummaryResponse | null>(null);
  const [models, setModels] = useState<ModelsConfig | null>(null);
  const [autonomy, setAutonomy] = useState<AutonomyConfig | null>(null);
  const [dataPolicy, setDataPolicy] = useState<DataPolicyConfig | null>(null);
  const [runtimeSettings, setRuntimeSettings] = useState<RuntimeSettingsResponse | null>(null);
  const [apiKeys, setApiKeys] = useState<ApiKeysStatusResponse | null>(null);
  const [roster, setRoster] = useState<AgentRosterConfig | null>(null);
  const [catalog, setCatalog] = useState<ProviderCatalogEntry[]>([]);
  const [openAiSub, setOpenAiSub] = useState<OpenAISubscriptionStatus | null>(null);
  const [keyModelsMap, setKeyModelsMap] = useState<Record<string, string[]>>({});
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
  const [settingsInitialTab, setSettingsInitialTab] = useState<SettingsTab>("general");

  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  const streamRef = useRef<RunEventStream | null>(null);

  const announce = useCallback((message: string) => {
    setAnnouncement("");
    window.requestAnimationFrame(() => setAnnouncement(message));
  }, []);

  const openSettingsAt = useCallback((tab: SettingsTab = "general") => {
    setSettingsInitialTab(tab);
    setSettingsOpen(true);
  }, []);

  const loadGlobal = useCallback(async () => {
    try {
      await probeDaemonHealth();
      const [
        projs,
        runList,
        chatList,
        modCfg,
        autoCfg,
        policyCfg,
        rtCfg,
        keysCfg,
        rosterCfg,
        catalogResp,
        subStatus,
      ] = await Promise.all([
        api.listProjects(),
        api.listRuns(),
        api.listChats(),
        api.getModels(),
        api.getAutonomy(),
        api.getDataPolicy(),
        api.getRuntimeSettings(),
        api.getApiKeys(),
        api.getAgentRoster(),
        api.getProviderCatalog(),
        api.getOpenAISubscriptionStatus(),
      ]);
      setProjects(projs);
      setRuns(runList);
      setChats(chatList);
      setModels(modCfg);
      setAutonomy(autoCfg);
      setDataPolicy(policyCfg);
      setRuntimeSettings(rtCfg);
      setApiKeys(keysCfg);
      setRoster(rosterCfg);
      setCatalog(catalogResp.providers);
      setOpenAiSub(subStatus);
      // Keep clean New Chat landing when selectedRunId is null; only clear if run disappeared
      setSelectedRunId((prev) =>
        prev && runList.some((r) => r.id === prev) ? prev : null
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadRunData = useCallback(async (runId: string) => {
    try {
      const [ag, tk, resv, ctr, dec, runMsgs, runNotes, cst, met] = await Promise.all([
        api.listAgents(runId),
        api.listTasks(runId),
        api.listReservations(runId),
        api.listContracts(runId),
        api.listDecisions(runId),
        api.listMessages(runId),
        api.listNotes(runId),
        api.getCosts(runId),
        api.getMetrics(runId),
      ]);
      setAgents(ag);
      setTasks(tk);
      setReservations(resv);
      setContracts(ctr);
      setDecisions(dec);
      setMessages(runMsgs);
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
      setMessages([]);
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
          ev.type.startsWith("chat.") ||
          ev.type.startsWith("agent.") ||
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

  const handleStartChat = async (payload: {
    prompt: string;
    project_id?: string;
    autonomy_preset?: AutonomyConfig["preset"];
  }) => {
    try {
      const created = await api.createRun(payload);
      setRuns((prev) => [created.run, ...prev.filter((r) => r.id !== created.run.id)]);
      setChats((prev) => [created.chat, ...prev.filter((c) => c.run_id !== created.run.id)]);
      setAgents(created.agents);
      setMessages(created.messages);
      setSelectedRunId(created.run.id);
      setSection("stream");
      setError(null);
      announce("Started new Foundry chat run");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleDeleteChat = async (runId: string) => {
    try {
      await api.deleteRun(runId);
      setChats((prev) => prev.filter((c) => c.run_id !== runId));
      setRuns((prev) => prev.filter((r) => r.id !== runId));
      if (selectedRunId === runId) {
        setSelectedRunId(null);
      }
      setError(null);
      announce("Deleted chat session");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handlePurgeTestRuns = async () => {
    try {
      const res = await api.purgeTestRuns();
      const dropSet = new Set(res.deleted_run_ids);
      setChats((prev) => prev.filter((c) => !dropSet.has(c.run_id)));
      setRuns((prev) => prev.filter((r) => !dropSet.has(r.id)));
      if (selectedRunId && dropSet.has(selectedRunId)) {
        setSelectedRunId(null);
      }
      setError(null);
      announce(`Cleared ${res.deleted_count} test runs`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

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

  const handleUpsertProfile = async (payload: ModelProfileUpsertPayload) => {
    try {
      const updated = await api.upsertModelProfile(payload);
      setModels(updated);
      setError(null);
      announce(`Saved model profile ${payload.profile_name}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      throw err;
    }
  };

  const handleDeleteProfile = async (profileName: string) => {
    try {
      const updated = await api.deleteModelProfile(profileName);
      setModels(updated);
      setError(null);
      announce(`Deleted profile ${profileName}`);
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

  const handleUnlockVault = async (passphrase: string) => {
    try {
      const status = await api.unlockVault(passphrase);
      setApiKeys(status);
      setError(null);
      announce("Vault unlocked");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      throw err;
    }
  };

  const handleSaveApiKey = async (payload: {
    name: string;
    secret: string;
    provider: string;
    passphrase?: string;
  }) => {
    try {
      await api.saveApiKey(payload);
      const updated = await api.getApiKeys();
      setApiKeys(updated);
      setError(null);
      announce(`Saved API key ${payload.name}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      throw err;
    }
  };

  const handleDeleteApiKey = async (name: string) => {
    try {
      await api.deleteApiKey(name);
      const [keysCfg, rosterCfg] = await Promise.all([api.getApiKeys(), api.getAgentRoster()]);
      setApiKeys(keysCfg);
      setRoster(rosterCfg);
      setError(null);
      announce(`Deleted API key ${name}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleFetchKeyModels = async (name: string): Promise<KeyModelsResponse> => {
    try {
      const res = await api.fetchKeyModels(name);
      setKeyModelsMap((prev) => ({ ...prev, [res.key_name]: res.models }));
      setError(null);
      return res;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      throw err;
    }
  };

  const handleToggleOpenAiSub = async (enabled: boolean) => {
    try {
      const status = await api.setOpenAISubscriptionEnabled(enabled);
      setOpenAiSub(status);
      const updatedModels = await api.getModels();
      setModels(updatedModels);
      setError(null);
      announce(enabled ? "OpenAI Subscription CLI enabled" : "OpenAI Subscription CLI disabled");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleTestConnection = async (payload: {
    auth_source: "api_key" | "openai_subscription";
    api_key_var?: string | null;
    model: string;
  }): Promise<TestConnectionResponse> => {
    try {
      const res = await api.testConnection(payload);
      setError(null);
      return res;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      return {
        ok: false,
        provider: payload.api_key_var ?? payload.auth_source,
        model: payload.model,
        transport: payload.auth_source,
        latency_ms: 0,
        reply_preview: "",
        error: msg,
      };
    }
  };

  const handleCreateAgentSlot = async (payload: {
    name: string;
    layer: AgentLayer;
    role: AgentRole;
    domain?: string | null;
    parent_id?: string | null;
    model_profile: string;
    auth_source?: AgentAuthSource;
    api_key_var?: string | null;
    model_name?: string | null;
    enabled?: boolean;
  }) => {
    try {
      const updated = await api.createAgentSlot(payload);
      setRoster(updated);
      const keysCfg = await api.getApiKeys();
      setApiKeys(keysCfg);
      setError(null);
      announce(`Added ${payload.name} to Layer ${payload.layer}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      throw err;
    }
  };

  const handleUpdateAgentSlot = async (
    agentId: string,
    payload: {
      model_profile?: string;
      auth_source?: AgentAuthSource;
      api_key_var?: string | null;
      model_name?: string | null;
      clear_api_key?: boolean;
      clear_model_name?: boolean;
      enabled?: boolean;
    }
  ) => {
    try {
      const updated = await api.updateAgentSlot(agentId, payload);
      setRoster(updated);
      const keysCfg = await api.getApiKeys();
      setApiKeys(keysCfg);
      setError(null);
      announce("Agent configuration updated");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleDeleteAgentSlot = async (agentId: string) => {
    try {
      const updated = await api.deleteAgentSlot(agentId);
      setRoster(updated);
      const keysCfg = await api.getApiKeys();
      setApiKeys(keysCfg);
      setError(null);
      announce("Removed agent from roster");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleAddRunAgent = async (
    runId: string,
    payload: {
      role: AgentRole;
      domain?: string | null;
      parent_agent_id?: string | null;
      model_profile: string;
    }
  ) => {
    try {
      const created = await api.addRunAgent(runId, payload);
      setAgents((prev) => [...prev, created]);
      announce(`Spawned ${payload.role} on run`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleRemoveRunAgent = async (runId: string, agentId: string) => {
    try {
      const updated = await api.removeRunAgent(runId, agentId);
      setAgents((prev) => prev.map((a) => (a.id === agentId ? updated : a)));
      announce("Removed agent from active run");
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

  const handleSendMessage = async (text: string, mode: "chat" | "ai" | "gotcha") => {
    if (!selectedRunId) return;
    try {
      if (mode === "gotcha") {
        const created = await api.createNote(selectedRunId, text, "gotcha");
        setNotes((prev) =>
          prev.some((n) => n.id === created.id) ? prev : [...prev, created]
        );
        announce("Gotcha persisted to Blackboard");
      } else {
        const invokeAi = mode === "ai";
        await api.postMessage(selectedRunId, text, "human", invokeAi);
        const latestMsgs = await api.listMessages(selectedRunId);
        setMessages(latestMsgs);
        if (invokeAi) {
          const cst = await api.getCosts(selectedRunId);
          setCosts(cst);
        }
        announce(invokeAi ? "Orchestrator replied" : "Message sent");
      }
      setSection("stream");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const activeRun = runs.find((r) => r.id === selectedRunId) ?? null;
  const activeChat = chats.find((c) => c.run_id === selectedRunId) ?? null;
  const cleanProject =
    projects.find((p) => !isTestProjectSlug(p.id) && !isTestProjectSlug(p.name)) ?? null;
  const activeProject =
    projects.find((p) => p.id === activeRun?.project_id) ?? cleanProject ?? null;
  const pendingDecisionsCount = decisions.filter((d) => d.status === "pending").length;
  const enabledAgentCount = (roster?.agents ?? []).filter((a) => a.enabled).length;
  const savedKeysCount = apiKeys?.keys.length ?? 0;

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
          if (next === "agents") {
            openInspector("agents");
            return;
          }
          setSection(next);
          if (next === "workspace") {
            setWorkspaceInitialTab("overview");
            setWorkspaceKey((k) => k + 1);
          }
        }}
        chats={chats}
        selectedRunId={selectedRunId}
        onNewChat={() => {
          setSelectedRunId(null);
          setSection("stream");
          setInspector(null);
        }}
        onSelectChat={(runId) => {
          setSelectedRunId(runId);
          setSection("stream");
        }}
        onDeleteChat={handleDeleteChat}
        onPurgeTestRuns={handlePurgeTestRuns}
        pendingDecisions={pendingDecisionsCount}
        enabledAgentCount={enabledAgentCount}
        theme={theme}
        onToggleTheme={handleToggleTheme}
        onOpenSettings={() => openSettingsAt("general")}
      />

      <main className="flex-1 min-w-0 flex flex-col h-full overflow-hidden relative">
        {/* Accessible Right-Sidebar Workbench Triggers */}
        <div className="absolute top-3.5 right-4 z-10 flex items-center gap-1.5">
          {(
            [
              {
                key: "agents" as const,
                label: `Agents (${enabledAgentCount})`,
                icon: Layers,
                title: "Open 3-layer Agents in right sidebar",
              },
              {
                key: "keys" as const,
                label: `API Keys (${savedKeysCount})`,
                icon: KeyRound,
                title: "Open API Keys & OpenAI Subscription CLI in right sidebar",
              },
              {
                key: "models" as const,
                label: "Models",
                icon: Bot,
                title: "Open editable Models in right sidebar",
              },
            ] as const
          ).map((btn) => {
            const Icon = btn.icon;
            const active = inspector === btn.key;
            return (
              <button
                key={btn.key}
                type="button"
                onClick={() => toggleInspector(btn.key)}
                title={btn.title}
                className="h-8 px-2.5 rounded-full border inline-flex items-center gap-1.5 text-[12px] transition-colors"
                style={
                  active
                    ? {
                        backgroundColor: "var(--bg-active)",
                        borderColor: "var(--accent)",
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
                <Icon className="w-3.5 h-3.5" style={{ color: "var(--accent)" }} />
                <span className="hidden sm:inline">{btn.label}</span>
              </button>
            );
          })}

          <button
            type="button"
            onClick={() => toggleInspector("decisions")}
            aria-label="Open run decisions, costs and outbox"
            title="Decisions, costs & outbox"
            className="h-8 w-8 rounded-full border flex items-center justify-center text-[var(--ink-secondary)] hover:bg-[var(--bg-hover)] transition-colors"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--border-hairline)",
            }}
          >
            <MoreHorizontal className="w-[17px] h-[17px]" />
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

        {section === "stream" && !activeRun && (
          <NewChatView
            defaultProjectName={cleanProject?.name ?? "app"}
            autonomy={autonomy}
            roster={roster}
            onAutonomyChange={async (preset) => handleAutonomyPatch({ preset })}
            onOpenAgentsStudio={() => openInspector("agents")}
            onStartChat={handleStartChat}
          />
        )}

        {section === "stream" && activeRun && (
          <>
            <ExecutionStream
              project={activeProject}
              run={activeRun}
              chatTitle={activeChat?.title ?? null}
              agents={agents}
              tasks={tasks}
              reservations={reservations}
              decisions={decisions}
              messages={messages}
              notes={notes}
              events={events}
              demoMode={demoMode}
              onOpenSettings={() => openSettingsAt("general")}
              onOpenAgentsStudio={() => openInspector("agents")}
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
              onSendMessage={handleSendMessage}
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
            roster={roster}
            apiKeys={apiKeys}
            catalog={catalog}
            openAiSub={openAiSub}
            keyModelsMap={keyModelsMap}
            onCreateAgentSlot={handleCreateAgentSlot}
            onUpdateAgentSlot={handleUpdateAgentSlot}
            onDeleteAgentSlot={handleDeleteAgentSlot}
            onAddRunAgent={handleAddRunAgent}
            onRemoveRunAgent={handleRemoveRunAgent}
            onOpenApiKeys={() => openInspector("keys")}
            onOpenDecisions={() => openInspector("decisions")}
            onAnnounce={announce}
          />
        )}

        {section === "agents" && (
          <AgentLayersStudio
            roster={roster}
            models={models}
            apiKeys={apiKeys}
            catalog={catalog}
            openAiSub={openAiSub}
            keyModelsMap={keyModelsMap}
            activeRunId={selectedRunId}
            runAgents={agents}
            onCreateAgentSlot={handleCreateAgentSlot}
            onUpdateAgentSlot={handleUpdateAgentSlot}
            onDeleteAgentSlot={handleDeleteAgentSlot}
            onAddRunAgent={handleAddRunAgent}
            onRemoveRunAgent={handleRemoveRunAgent}
            onOpenApiKeys={() => openInspector("keys")}
          />
        )}

        {section === "controls" && (
          <TelemetryHub
            costs={costs}
            metrics={metrics}
            onOpenModelsDrawer={() => openInspector("models")}
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
          activeRunId={selectedRunId}
          runAgents={agents}
          roster={roster}
          apiKeys={apiKeys}
          catalog={catalog}
          openAiSub={openAiSub}
          keyModelsMap={keyModelsMap}
          onCreateAgentSlot={handleCreateAgentSlot}
          onUpdateAgentSlot={handleUpdateAgentSlot}
          onDeleteAgentSlot={handleDeleteAgentSlot}
          onAddRunAgent={handleAddRunAgent}
          onRemoveRunAgent={handleRemoveRunAgent}
          onUnlockVault={handleUnlockVault}
          onSaveApiKey={handleSaveApiKey}
          onDeleteApiKey={handleDeleteApiKey}
          onFetchKeyModels={handleFetchKeyModels}
          onToggleOpenAiSub={handleToggleOpenAiSub}
          onTestConnection={handleTestConnection}
          decisions={decisions}
          onResolveDecision={handleResolveDecision}
          models={models}
          dataPolicy={dataPolicy}
          onUpdateRole={handleUpdateRole}
          onUpsertProfile={handleUpsertProfile}
          onDeleteProfile={handleDeleteProfile}
          onUpdateDataPolicy={handleUpdateDataPolicy}
          costs={costs}
          metrics={metrics}
          events={events}
        />
      )}

      <SettingsModal
        open={settingsOpen}
        initialTab={settingsInitialTab}
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
        dataPolicy={dataPolicy}
        onUpdateDataPolicy={handleUpdateDataPolicy}
        runtimeSettings={runtimeSettings}
        onOpenWorkbench={(target) => openInspector(target)}
      />
    </div>
  );
}

export default App;
