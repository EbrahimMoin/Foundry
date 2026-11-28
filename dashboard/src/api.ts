import {
  DEFAULT_PROVIDER_CATALOG,
  INITIAL_MOCK_AGENTS,
  INITIAL_MOCK_AUTONOMY,
  INITIAL_MOCK_CHATS,
  INITIAL_MOCK_CONTRACTS,
  INITIAL_MOCK_COSTS,
  INITIAL_MOCK_DATA_POLICY,
  INITIAL_MOCK_DECISIONS,
  INITIAL_MOCK_EVENTS,
  INITIAL_MOCK_KEYS,
  INITIAL_MOCK_MESSAGES,
  INITIAL_MOCK_METRICS,
  INITIAL_MOCK_MODELS,
  INITIAL_MOCK_NOTES,
  INITIAL_MOCK_OPENAI_SUBSCRIPTION,
  INITIAL_MOCK_PREVIEWS,
  INITIAL_MOCK_RESERVATIONS,
  INITIAL_MOCK_ROSTER,
  INITIAL_MOCK_RUNS,
  INITIAL_MOCK_RUNTIME_SETTINGS,
  MOCK_CONTRACT_VERSIONS,
  MOCK_PROJECTS,
  MOCK_TASK_WRITE_SETS,
  INITIAL_MOCK_TASKS,
  PRIMARY_RUN_ID,
} from "./mockData";
import type {
  AgentAuthSource,
  AgentLayer,
  AgentRecord,
  AgentRole,
  AgentRosterConfig,
  AgentSlotConfig,
  ApiKeyPublicRecord,
  ApiKeysStatusResponse,
  AutonomyConfig,
  AutonomyOverrides,
  BlackboardEvent,
  ChatRunCreatedResponse,
  ChatSessionSummary,
  ContractRecord,
  ContractVersionsResponse,
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
  PreviewUrlResponse,
  ProjectSummary,
  ProviderCatalogEntry,
  ProviderCatalogResponse,
  PurgeTestRunsResponse,
  RunRecord,
  RuntimeSettingsResponse,
  TaskDetailResponse,
  TaskRecord,
  TestConnectionResponse,
  WriteReservationRecord,
} from "./types";
import { normalizeProfileKey } from "./utils/foundryMetrics";

/**
 * Interactive demo fallback store active ONLY when the Foundry FastAPI daemon
 * is offline or the user explicitly switches to Demo Preview inside Settings.
 */
class MockFoundryStore {
  projects: ProjectSummary[] = structuredClone(MOCK_PROJECTS);
  runs: RunRecord[] = structuredClone(INITIAL_MOCK_RUNS);
  agents: Record<string, AgentRecord[]> = structuredClone(INITIAL_MOCK_AGENTS);
  tasks: Record<string, TaskRecord[]> = structuredClone(INITIAL_MOCK_TASKS);
  reservations: Record<string, WriteReservationRecord[]> = structuredClone(INITIAL_MOCK_RESERVATIONS);
  contracts: Record<string, ContractRecord[]> = structuredClone(INITIAL_MOCK_CONTRACTS);
  decisions: Record<string, DecisionRecord[]> = structuredClone(INITIAL_MOCK_DECISIONS);
  notes: Record<string, NoteRecord[]> = structuredClone(INITIAL_MOCK_NOTES);
  chats: ChatSessionSummary[] = structuredClone(INITIAL_MOCK_CHATS);
  messages: Record<string, MessageRecord[]> = structuredClone(INITIAL_MOCK_MESSAGES);
  roster: AgentRosterConfig = structuredClone(INITIAL_MOCK_ROSTER);
  keysStatus: ApiKeysStatusResponse = structuredClone(INITIAL_MOCK_KEYS);
  costs: Record<string, CostsSummaryResponse> = structuredClone(INITIAL_MOCK_COSTS);
  metrics: Record<string, MetricsSummaryResponse> = structuredClone(INITIAL_MOCK_METRICS);
  models: ModelsConfig = structuredClone(INITIAL_MOCK_MODELS);
  autonomy: AutonomyConfig = structuredClone(INITIAL_MOCK_AUTONOMY);
  dataPolicy: DataPolicyConfig = structuredClone(INITIAL_MOCK_DATA_POLICY);
  runtimeSettings: RuntimeSettingsResponse = structuredClone(INITIAL_MOCK_RUNTIME_SETTINGS);
  events: Record<string, BlackboardEvent[]> = structuredClone(INITIAL_MOCK_EVENTS);
  previews = structuredClone(INITIAL_MOCK_PREVIEWS);
  catalog: ProviderCatalogEntry[] = structuredClone(DEFAULT_PROVIDER_CATALOG);
  openAiSub: OpenAISubscriptionStatus = structuredClone(INITIAL_MOCK_OPENAI_SUBSCRIPTION);

  private listeners = new Map<string, Set<(ev: BlackboardEvent) => void>>();

  subscribe(runId: string, fn: (ev: BlackboardEvent) => void): () => void {
    const set = this.listeners.get(runId) ?? new Set();
    set.add(fn);
    this.listeners.set(runId, set);
    return () => {
      set.delete(fn);
    };
  }

  emitEvent(
    runId: string,
    type: string,
    subjectId: string | null,
    payload: Record<string, unknown>
  ): BlackboardEvent {
    const list = this.events[runId] ?? [];
    const lastSeq = list.reduce((max, e) => Math.max(max, e.publish_seq ?? e.id), 400);
    const nextSeq = lastSeq + 1;
    const now = new Date().toISOString();
    const ev: BlackboardEvent = {
      id: nextSeq,
      publish_seq: nextSeq,
      run_id: runId,
      type,
      subject_id: subjectId,
      payload,
      created_at: now,
      published_at: now,
    };
    this.events[runId] = [...list, ev];
    const m = this.metrics[runId];
    if (m) {
      m.events_count = this.events[runId].length;
    }
    const subs = this.listeners.get(runId);
    if (subs) {
      for (const fn of subs) fn(ev);
    }
    return ev;
  }

  recomputeMetrics(runId: string): void {
    const run = this.runs.find((r) => r.id === runId);
    const runTasks = this.tasks[runId] ?? [];
    const runDecisions = this.decisions[runId] ?? [];
    const runReservations = (this.reservations[runId] ?? []).filter((r) => !r.released_at);
    const runContracts = this.contracts[runId] ?? [];
    const byStatus: Record<string, number> = {};
    let totalAttempts = 0;
    let totalReopens = 0;
    for (const t of runTasks) {
      byStatus[t.status] = (byStatus[t.status] ?? 0) + 1;
      totalAttempts += t.attempt;
      totalReopens += t.contract_reopens;
    }
    const pendingCount = runDecisions.filter((d) => d.status === "pending").length;
    const existing = this.metrics[runId];
    if (existing) {
      existing.phase = run?.phase ?? existing.phase;
      existing.tasks_total = runTasks.length;
      existing.tasks_by_status = byStatus;
      existing.total_attempts = totalAttempts;
      existing.total_contract_reopens = totalReopens;
      existing.active_reservations = runReservations.length;
      existing.contracts_count = runContracts.length;
      existing.pending_decisions = pendingCount;
      existing.events_count = (this.events[runId] ?? []).length;
    }
  }
}

const mockStore = new MockFoundryStore();

let daemonOnline = true;
let manualDemoMode = false;
const modeListeners = new Set<(state: { demoMode: boolean; daemonOnline: boolean }) => void>();

function notifyMode(): void {
  const snapshot = { demoMode: isDemoMode(), daemonOnline };
  for (const listener of modeListeners) listener(snapshot);
}

export function isDemoMode(): boolean {
  return manualDemoMode || !daemonOnline;
}

export function isDaemonOnline(): boolean {
  return daemonOnline;
}

export function setManualDemoMode(enabled: boolean): void {
  manualDemoMode = enabled;
  notifyMode();
}

export function subscribeConnectionMode(
  listener: (state: { demoMode: boolean; daemonOnline: boolean }) => void
): () => void {
  modeListeners.add(listener);
  return () => modeListeners.delete(listener);
}

export async function probeDaemonHealth(): Promise<boolean> {
  try {
    const res = await fetch("/api/health", { cache: "no-store" });
    const ct = res.headers.get("content-type") ?? "";
    const ok = res.ok && ct.includes("application/json");
    if (ok) {
      const payload = (await res.json()) as { status?: string };
      daemonOnline = payload.status === "ok";
    } else {
      daemonOnline = false;
    }
  } catch {
    daemonOnline = false;
  }
  notifyMode();
  return daemonOnline;
}

async function request<T>(
  path: string,
  init: RequestInit | undefined,
  mockHandler: () => T
): Promise<T> {
  if (isDemoMode()) {
    return structuredClone(mockHandler());
  }
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      ...init,
    });
    const contentType = res.headers.get("content-type") ?? "";
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        if (contentType.includes("application/json")) {
          const errJson = (await res.json()) as { detail?: string };
          if (errJson.detail) detail = errJson.detail;
        }
      } catch {
        // fallback to status text
      }
      throw new Error(detail);
    }
    if (!contentType.includes("application/json")) {
      throw new Error(`Unexpected response type from daemon for ${path}`);
    }
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof TypeError) {
      // Network-level failure (daemon unreachable)
      daemonOnline = false;
      notifyMode();
      return structuredClone(mockHandler());
    }
    throw err;
  }
}

export const api = {
  listProjects: () =>
    request<ProjectSummary[]>("/api/projects", undefined, () => mockStore.projects),

  listRuns: () => request<RunRecord[]>("/api/runs", undefined, () => mockStore.runs),

  listChats: () =>
    request<ChatSessionSummary[]>("/api/chats", undefined, () => {
      const assignedRefresh = mockStore.chats.map((c) => ({
        ...c,
        message_count: (mockStore.messages[c.run_id] ?? []).length,
        agent_count: (mockStore.agents[c.run_id] ?? []).length,
      }));
      return assignedRefresh;
    }),

  createRun: (payload: {
    prompt: string;
    title?: string;
    project_id?: string;
    autonomy_preset?: AutonomyConfig["preset"];
    budget_usd?: number;
  }) =>
    request<ChatRunCreatedResponse>(
      "/api/runs",
      { method: "POST", body: JSON.stringify(payload) },
      () => {
        const runId = `run_${Date.now()}`;
        const now = new Date().toISOString();
        const projectId = payload.project_id?.trim() || "app";
        const firstLine = payload.prompt.trim().split(/\r?\n/)[0] || "New run";
        const title =
          payload.title?.trim() ||
          (firstLine.length > 56 ? `${firstLine.slice(0, 56)}…` : firstLine);
        const run: RunRecord = {
          id: runId,
          project_id: projectId,
          phase: "planning",
          budget_usd: payload.budget_usd ?? 5.0,
          spent_usd: 0,
          data_policy: { ...mockStore.dataPolicy },
          started_at: now,
          ended_at: null,
        };
        const threadId = `chat_${runId}`;
        const enabled = mockStore.roster.agents.filter((a) => a.enabled);
        const agents: AgentRecord[] = enabled.map((slot, idx) => ({
          id: `agt_${runId}_${idx}`,
          run_id: runId,
          role: slot.role,
          domain: slot.domain,
          parent_agent_id: null,
          model_profile: slot.model_profile,
          status: "idle",
          graph_thread_id: `${runId}:layer${slot.layer}:${slot.id}`,
        }));
        const msgs: MessageRecord[] = [
          {
            id: `msg_${Date.now()}_1`,
            thread_id: threadId,
            from_id: "human",
            to_id: "orchestrator",
            body: payload.prompt.trim(),
            created_at: now,
          },
          {
            id: `msg_${Date.now()}_2`,
            thread_id: threadId,
            from_id: "orchestrator",
            to_id: "human",
            body: `Initialized run ${runId} for ${projectId} with ${agents.length} configured 3-layer agents. Ready for planning.`,
            created_at: now,
          },
        ];
        const summary: ChatSessionSummary = {
          run_id: runId,
          title,
          project_id: projectId,
          phase: "planning",
          message_count: msgs.length,
          agent_count: agents.length,
          is_chat_session: true,
          started_at: now,
          ended_at: null,
        };
        mockStore.runs = [run, ...mockStore.runs];
        mockStore.chats = [summary, ...mockStore.chats];
        mockStore.messages[runId] = msgs;
        mockStore.agents[runId] = agents;
        mockStore.tasks[runId] = [];
        mockStore.reservations[runId] = [];
        mockStore.contracts[runId] = [];
        mockStore.decisions[runId] = [];
        mockStore.notes[runId] = [];
        mockStore.recomputeMetrics(runId);
        return { run, chat: summary, messages: msgs, agents };
      }
    ),

  listMessages: (runId: string) =>
    request<MessageRecord[]>(
      `/api/runs/${runId}/messages`,
      undefined,
      () => mockStore.messages[runId] ?? []
    ),

  postMessage: (
    runId: string,
    body: string,
    fromId: "human" | "orchestrator" = "human",
    invokeOrchestrator = false
  ) =>
    request<MessageRecord>(
      `/api/runs/${runId}/messages`,
      {
        method: "POST",
        body: JSON.stringify({
          body,
          from_id: fromId,
          invoke_orchestrator: invokeOrchestrator,
        }),
      },
      () => {
        const msg: MessageRecord = {
          id: `msg_${Date.now()}`,
          thread_id: `chat_${runId}`,
          from_id: fromId,
          to_id: fromId === "human" ? "orchestrator" : "human",
          body,
          created_at: new Date().toISOString(),
        };
        mockStore.messages[runId] = [...(mockStore.messages[runId] ?? []), msg];
        mockStore.emitEvent(runId, "chat.message_posted", msg.id, {
          message_id: msg.id,
          from_id: msg.from_id,
          body: msg.body,
        });
        if (invokeOrchestrator && fromId === "human") {
          const reply: MessageRecord = {
            id: `msg_${Date.now()}_reply`,
            thread_id: `chat_${runId}`,
            from_id: "orchestrator",
            to_id: "human",
            body: `[Demo preview] Orchestrator received: "${body.slice(0, 80)}". Connect the live daemon to call your configured model.`,
            created_at: new Date().toISOString(),
          };
          mockStore.messages[runId] = [...(mockStore.messages[runId] ?? []), reply];
          mockStore.emitEvent(runId, "chat.message_posted", reply.id, {
            message_id: reply.id,
            from_id: "orchestrator",
            body: reply.body,
          });
        }
        return msg;
      }
    ),

  deleteRun: (runId: string) =>
    request<{ deleted: string }>(
      `/api/runs/${runId}`,
      { method: "DELETE" },
      () => {
        mockStore.runs = mockStore.runs.filter((r) => r.id !== runId);
        mockStore.chats = mockStore.chats.filter((c) => c.run_id !== runId);
        delete mockStore.agents[runId];
        delete mockStore.tasks[runId];
        delete mockStore.reservations[runId];
        delete mockStore.contracts[runId];
        delete mockStore.decisions[runId];
        delete mockStore.messages[runId];
        delete mockStore.notes[runId];
        delete mockStore.events[runId];
        return { deleted: runId };
      }
    ),

  purgeTestRuns: () =>
    request<PurgeTestRunsResponse>(
      "/api/chats/purge-test-runs",
      { method: "POST" },
      () => {
        const nonChatIds = mockStore.chats.filter((c) => !c.is_chat_session).map((c) => c.run_id);
        const dropSet = new Set(nonChatIds);
        mockStore.runs = mockStore.runs.filter((r) => !dropSet.has(r.id));
        mockStore.chats = mockStore.chats.filter((c) => c.is_chat_session);
        return { deleted_count: nonChatIds.length, deleted_run_ids: nonChatIds };
      }
    ),

  pauseRun: (runId: string) =>
    request<{ run_id: string; phase: string }>(
      `/api/runs/${runId}/pause`,
      { method: "POST" },
      () => {
        const run = mockStore.runs.find((r) => r.id === runId);
        if (run) {
          run.phase = "paused";
          mockStore.recomputeMetrics(runId);
          mockStore.emitEvent(runId, "run.paused", runId, { run_id: runId, phase: "paused" });
        }
        return { run_id: runId, phase: "paused" };
      }
    ),

  resumeRun: (runId: string) =>
    request<{ run_id: string; phase: string }>(
      `/api/runs/${runId}/resume`,
      { method: "POST" },
      () => {
        const run = mockStore.runs.find((r) => r.id === runId);
        const pendingDecisions = (mockStore.decisions[runId] ?? []).filter(
          (d) => d.status === "pending"
        ).length;
        const nextPhase = pendingDecisions > 0 ? "waiting_decision" : "building";
        if (run) {
          run.phase = nextPhase;
          mockStore.recomputeMetrics(runId);
          mockStore.emitEvent(runId, "run.resumed", runId, { run_id: runId, phase: nextPhase });
        }
        return { run_id: runId, phase: nextPhase };
      }
    ),

  stopRun: (runId: string) =>
    request<{ run_id: string; phase: string }>(
      `/api/runs/${runId}/stop`,
      { method: "POST" },
      () => {
        const run = mockStore.runs.find((r) => r.id === runId);
        if (run) {
          run.phase = "stopped";
          run.ended_at = new Date().toISOString();
          mockStore.recomputeMetrics(runId);
          mockStore.emitEvent(runId, "run.stopped", runId, { run_id: runId, phase: "stopped" });
        }
        return { run_id: runId, phase: "stopped" };
      }
    ),

  listAgents: (runId: string) =>
    request<AgentRecord[]>(
      `/api/runs/${runId}/agents`,
      undefined,
      () => mockStore.agents[runId] ?? []
    ),

  addRunAgent: (
    runId: string,
    payload: {
      role: AgentRole;
      domain?: string | null;
      parent_agent_id?: string | null;
      model_profile: string;
    }
  ) =>
    request<AgentRecord>(
      `/api/runs/${runId}/agents`,
      {
        method: "POST",
        body: JSON.stringify({
          ...payload,
          model_profile: normalizeProfileKey(payload.model_profile),
        }),
      },
      () => {
        const agt: AgentRecord = {
          id: `agt_${Date.now()}`,
          run_id: runId,
          role: payload.role,
          domain: payload.domain ?? null,
          parent_agent_id: payload.parent_agent_id ?? null,
          model_profile: normalizeProfileKey(payload.model_profile),
          status: "idle",
          graph_thread_id: `${runId}:agent:${Date.now()}`,
        };
        mockStore.agents[runId] = [...(mockStore.agents[runId] ?? []), agt];
        return agt;
      }
    ),

  removeRunAgent: (runId: string, agentId: string) =>
    request<AgentRecord>(
      `/api/runs/${runId}/agents/${agentId}`,
      { method: "DELETE" },
      () => {
        const list = mockStore.agents[runId] ?? [];
        const updated = list.map((a) =>
          a.id === agentId ? { ...a, status: "removed" } : a
        );
        mockStore.agents[runId] = updated;
        return updated.find((a) => a.id === agentId)!;
      }
    ),

  listTasks: (runId: string) =>
    request<TaskRecord[]>(
      `/api/runs/${runId}/tasks`,
      undefined,
      () => mockStore.tasks[runId] ?? []
    ),

  getTaskDetail: (taskId: string) =>
    request<TaskDetailResponse>(`/api/tasks/${taskId}`, undefined, () => {
      for (const [runId, list] of Object.entries(mockStore.tasks)) {
        const task = list.find((t) => t.id === taskId);
        if (task) {
          const activeLocks = (mockStore.reservations[runId] ?? []).filter(
            (r) => r.task_id === taskId && !r.released_at
          );
          return {
            task,
            write_set: MOCK_TASK_WRITE_SETS[task.id] ?? activeLocks.map((r) => r.path),
            dependencies: task.pending_deps ?? [],
            reservations: activeLocks,
          };
        }
      }
      throw new Error(`Task not found: ${taskId}`);
    }),

  listReservations: (runId: string) =>
    request<WriteReservationRecord[]>(
      `/api/runs/${runId}/reservations`,
      undefined,
      () => mockStore.reservations[runId] ?? []
    ),

  listContracts: (runId: string) =>
    request<ContractRecord[]>(
      `/api/runs/${runId}/contracts`,
      undefined,
      () => mockStore.contracts[runId] ?? []
    ),

  getContractVersions: (contractId: string) =>
    request<ContractVersionsResponse>(
      `/api/contracts/${contractId}/versions`,
      undefined,
      () => {
        const found = MOCK_CONTRACT_VERSIONS[contractId];
        if (found) return found;
        return {
          contract_id: contractId,
          name: contractId,
          domain: "backend",
          kind: "openapi",
          status: "locked",
          current_version: 1,
          versions: [],
        };
      }
    ),

  listDecisions: (runId: string) =>
    request<DecisionRecord[]>(
      `/api/runs/${runId}/decisions`,
      undefined,
      () => mockStore.decisions[runId] ?? []
    ),

  resolveDecision: (
    decisionId: string,
    body: { action: "approve" | "reject"; choice?: unknown; feedback?: string }
  ) =>
    request<DecisionRecord>(
      `/api/decisions/${decisionId}/resolve`,
      { method: "POST", body: JSON.stringify(body) },
      () => {
        for (const [runId, list] of Object.entries(mockStore.decisions)) {
          const dec = list.find((d) => d.id === decisionId);
          if (dec) {
            dec.status = "resolved";
            dec.decided_by = "human";
            dec.decided_at = new Date().toISOString();
            dec.answer = {
              action: body.action,
              choice:
                body.choice ??
                (body.action === "approve"
                  ? dec.recommendation ?? dec.options[0]
                  : "reject"),
              feedback: body.feedback ?? null,
            };
            const remaining = list.filter((d) => d.status === "pending").length;
            const run = mockStore.runs.find((r) => r.id === runId);
            if (run && remaining === 0 && run.phase === "waiting_decision") {
              run.phase = "building";
            }
            mockStore.recomputeMetrics(runId);
            mockStore.emitEvent(runId, "decision.resolved", dec.id, {
              action: body.action,
              choice: dec.answer.choice,
            });
            return dec;
          }
        }
        throw new Error(`Decision ${decisionId} not found`);
      }
    ),

  listNotes: (runId: string) =>
    request<NoteRecord[]>(
      `/api/runs/${runId}/notes`,
      undefined,
      () => mockStore.notes[runId] ?? []
    ),

  createNote: (runId: string, body: string, kind: "note" | "gotcha" = "note") =>
    request<NoteRecord>(
      `/api/runs/${runId}/notes`,
      { method: "POST", body: JSON.stringify({ body, kind }) },
      () => {
        const note: NoteRecord = {
          id: `note_${Date.now()}`,
          run_id: runId,
          task_id: null,
          feature: null,
          kind,
          body,
          created_at: new Date().toISOString(),
        };
        mockStore.notes[runId] = [...(mockStore.notes[runId] ?? []), note];
        mockStore.emitEvent(runId, "run.note_logged", note.id, {
          note_id: note.id,
          kind: note.kind,
          body: note.body,
        });
        return note;
      }
    ),

  getCosts: (runId: string) =>
    request<CostsSummaryResponse>(
      `/api/runs/${runId}/costs`,
      undefined,
      () =>
        mockStore.costs[runId] ?? {
          run_id: runId,
          budget_usd: 5,
          spent_usd: 0,
          total_call_cost_usd: 0,
          total_est_value_usd: 0,
          call_count: 0,
          cache_hit_rate: 0,
          by_model: [],
          by_profile: [],
        }
    ),

  getMetrics: (runId: string) =>
    request<MetricsSummaryResponse>(
      `/api/runs/${runId}/metrics`,
      undefined,
      () => {
        mockStore.recomputeMetrics(runId);
        return mockStore.metrics[runId];
      }
    ),

  getModels: () =>
    request<ModelsConfig>("/api/settings/models", undefined, () => mockStore.models),

  updateModelAssignments: (roles: Record<string, string>) => {
    const normalized: Record<string, string> = {};
    for (const [role, profile] of Object.entries(roles)) {
      normalized[role] = normalizeProfileKey(profile);
    }
    return request<ModelsConfig>(
      "/api/settings/models/assignments",
      { method: "PUT", body: JSON.stringify({ roles: normalized }) },
      () => {
        mockStore.models.assignments.roles = {
          ...mockStore.models.assignments.roles,
          ...normalized,
        };
        mockStore.emitEvent(PRIMARY_RUN_ID, "config.models_updated", "models", {
          roles: normalized,
        });
        return mockStore.models;
      }
    );
  },

  getAutonomy: () =>
    request<AutonomyConfig>("/api/settings/autonomy", undefined, () => mockStore.autonomy),

  updateAutonomy: (patch: {
    preset?: AutonomyConfig["preset"];
    overrides?: AutonomyOverrides;
    confidence_threshold?: number;
  }) =>
    request<AutonomyConfig>(
      "/api/settings/autonomy",
      { method: "PUT", body: JSON.stringify(patch) },
      () => {
        mockStore.autonomy = {
          ...mockStore.autonomy,
          ...(patch.preset !== undefined ? { preset: patch.preset } : {}),
          ...(patch.overrides !== undefined ? { overrides: patch.overrides } : {}),
          ...(patch.confidence_threshold !== undefined
            ? { confidence_threshold: patch.confidence_threshold }
            : {}),
        };
        mockStore.emitEvent(PRIMARY_RUN_ID, "config.autonomy_updated", "autonomy", patch);
        return mockStore.autonomy;
      }
    ),

  getDataPolicy: () =>
    request<DataPolicyConfig>("/api/settings/data-policy", undefined, () => mockStore.dataPolicy),

  updateDataPolicy: (policy: Partial<DataPolicyConfig>) =>
    request<DataPolicyConfig>(
      "/api/settings/data-policy",
      { method: "PUT", body: JSON.stringify(policy) },
      () => {
        mockStore.dataPolicy = { ...mockStore.dataPolicy, ...policy };
        mockStore.emitEvent(PRIMARY_RUN_ID, "config.data_policy_updated", "data_policy", {
          ...mockStore.dataPolicy,
        });
        return mockStore.dataPolicy;
      }
    ),

  getRuntimeSettings: () =>
    request<RuntimeSettingsResponse>(
      "/api/settings/runtime",
      undefined,
      () => mockStore.runtimeSettings
    ),

  getApiKeys: () =>
    request<ApiKeysStatusResponse>("/api/settings/keys", undefined, () => {
      const keys = mockStore.keysStatus.keys.map((k) => ({
        ...k,
        assigned_agents: mockStore.roster.agents
          .filter((a) => a.api_key_var === k.name)
          .map((a) => a.name),
      }));
      return { ...mockStore.keysStatus, keys };
    }),

  unlockVault: (passphrase: string) =>
    request<ApiKeysStatusResponse>(
      "/api/settings/vault/unlock",
      { method: "POST", body: JSON.stringify({ passphrase }) },
      () => {
        mockStore.keysStatus.vault_unlocked = true;
        return mockStore.keysStatus;
      }
    ),

  getProviderCatalog: () =>
    request<ProviderCatalogResponse>(
      "/api/settings/catalog",
      undefined,
      () => ({ providers: mockStore.catalog })
    ),

  saveApiKey: (payload: {
    name: string;
    secret: string;
    provider: string;
    passphrase?: string;
  }) =>
    request<ApiKeyPublicRecord>(
      "/api/settings/keys",
      { method: "POST", body: JSON.stringify(payload) },
      () => {
        const normName = payload.name.trim().replace(/[\s-]+/g, "_").toUpperCase();
        const s = payload.secret.trim();
        const masked = s.length >= 10 ? `${s.slice(0, 3)}••••${s.slice(-4)}` : "••••";
        const cat = mockStore.catalog.find((p) => p.id === payload.provider);
        const rec: ApiKeyPublicRecord = {
          name: normName,
          provider: payload.provider,
          provider_label: cat?.label ?? payload.provider,
          key_ref: `vault:var_${normName.toLowerCase()}`,
          masked_preview: masked,
          backend: "encrypted_vault",
          assigned_agents: mockStore.roster.agents
            .filter((a) => a.api_key_var === normName)
            .map((a) => a.name),
          created_at: new Date().toISOString(),
        };
        mockStore.keysStatus.keys = [
          ...mockStore.keysStatus.keys.filter((k) => k.name !== normName),
          rec,
        ].sort((a, b) => a.name.localeCompare(b.name));
        mockStore.keysStatus.vault_unlocked = true;
        return rec;
      }
    ),

  fetchKeyModels: (keyName: string) =>
    request<KeyModelsResponse>(
      `/api/settings/keys/${encodeURIComponent(keyName)}/models`,
      { method: "POST" },
      () => {
        const entry = mockStore.keysStatus.keys.find((k) => k.name === keyName);
        const cat = mockStore.catalog.find((p) => p.id === entry?.provider);
        return {
          key_name: keyName,
          provider: entry?.provider ?? "custom",
          provider_label: entry?.provider_label ?? "Custom",
          models: cat?.models ?? [],
          source: "catalog_fallback",
          detail: "Demo catalog",
        };
      }
    ),

  upsertModelProfile: (payload: ModelProfileUpsertPayload) =>
    request<ModelsConfig>(
      "/api/settings/models/profiles",
      { method: "PUT", body: JSON.stringify(payload) },
      () => {
        const norm = `@${payload.profile_name.trim().replace(/^@+/, "")}`;
        let providerId = payload.provider ?? "gemini_paid";
        let family = "custom";
        if (payload.auth_source === "openai_subscription") {
          providerId = "openai_subscription_cli";
          family = "openai";
        } else if (payload.auth_source === "api_key" && payload.api_key_var) {
          const entry = mockStore.keysStatus.keys.find((k) => k.name === payload.api_key_var);
          const cat = mockStore.catalog.find((c) => c.id === entry?.provider);
          providerId = cat?.default_provider_id ?? "openai_paid";
          family = cat?.family ?? entry?.provider ?? "custom";
        } else if (payload.provider) {
          const cat = mockStore.catalog.find(
            (c) => c.id === payload.provider || c.default_provider_id === payload.provider
          );
          if (cat) {
            providerId = cat.default_provider_id;
            family = cat.family;
          }
        }
        mockStore.models = {
          ...mockStore.models,
          profiles: {
            ...mockStore.models.profiles,
            [norm]: {
              provider: providerId,
              model: payload.model,
              family,
              price: {
                in: payload.input_per_m ?? 0,
                cached_in: payload.cached_input_per_m ?? 0,
                out: payload.output_per_m ?? 0,
              },
              fallbacks: payload.fallbacks ?? [],
            },
          },
        };
        return mockStore.models;
      }
    ),

  deleteModelProfile: (profileName: string) =>
    request<ModelsConfig>(
      `/api/settings/models/profiles/${encodeURIComponent(profileName)}`,
      { method: "DELETE" },
      () => {
        const norm = `@${profileName.trim().replace(/^@+/, "")}`;
        const nextProfiles = { ...mockStore.models.profiles };
        delete nextProfiles[norm];
        mockStore.models = { ...mockStore.models, profiles: nextProfiles };
        return mockStore.models;
      }
    ),

  getOpenAISubscriptionStatus: () =>
    request<OpenAISubscriptionStatus>(
      "/api/settings/subscriptions/openai",
      undefined,
      () => mockStore.openAiSub
    ),

  setOpenAISubscriptionEnabled: (enabled: boolean) =>
    request<OpenAISubscriptionStatus>(
      "/api/settings/subscriptions/openai",
      { method: "PUT", body: JSON.stringify({ enabled }) },
      () => {
        mockStore.openAiSub = { ...mockStore.openAiSub, enabled };
        if (mockStore.models.policies) {
          mockStore.models.policies.allow_openai_subscription_cli = enabled;
        }
        return mockStore.openAiSub;
      }
    ),

  testConnection: (payload: {
    auth_source: "api_key" | "openai_subscription";
    api_key_var?: string | null;
    model: string;
    prompt?: string;
  }) =>
    request<TestConnectionResponse>(
      "/api/settings/test-connection",
      { method: "POST", body: JSON.stringify(payload) },
      () => ({
        ok: false,
        provider:
          payload.auth_source === "openai_subscription"
            ? "OpenAI Subscription (Codex CLI)"
            : payload.api_key_var ?? "api_key",
        model: payload.model,
        transport:
          payload.auth_source === "openai_subscription" ? "openai_codex_cli" : "openai_compat",
        latency_ms: 0,
        reply_preview: "",
        error: "Demo preview mode — start Foundry daemon to test live provider or Codex CLI calls.",
      })
    ),

  deleteApiKey: (name: string) =>
    request<{ deleted: string }>(
      `/api/settings/keys/${encodeURIComponent(name)}`,
      { method: "DELETE" },
      () => {
        mockStore.keysStatus.keys = mockStore.keysStatus.keys.filter((k) => k.name !== name);
        mockStore.roster.agents = mockStore.roster.agents.map((a) =>
          a.api_key_var === name
            ? { ...a, auth_source: "profile_default", api_key_var: null, model_name: null }
            : a
        );
        return { deleted: name };
      }
    ),

  getAgentRoster: () =>
    request<AgentRosterConfig>(
      "/api/settings/agents",
      undefined,
      () => mockStore.roster
    ),

  createAgentSlot: (payload: {
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
  }) =>
    request<AgentRosterConfig>(
      "/api/settings/agents",
      {
        method: "POST",
        body: JSON.stringify({
          ...payload,
          model_profile: normalizeProfileKey(payload.model_profile),
        }),
      },
      () => {
        const authSource: AgentAuthSource =
          payload.auth_source ?? (payload.api_key_var ? "api_key" : "profile_default");
        const slot: AgentSlotConfig = {
          id: `l${payload.layer}_${Date.now()}`,
          name: payload.name.trim(),
          layer: payload.layer,
          role: payload.role,
          domain: payload.domain ?? null,
          parent_id: payload.parent_id ?? null,
          model_profile: normalizeProfileKey(payload.model_profile),
          auth_source: authSource,
          api_key_var: authSource === "api_key" ? payload.api_key_var ?? null : null,
          model_name: payload.model_name ?? null,
          enabled: payload.enabled ?? true,
        };
        mockStore.roster = { agents: [...mockStore.roster.agents, slot] };
        return mockStore.roster;
      }
    ),

  updateAgentSlot: (
    agentId: string,
    payload: {
      name?: string;
      layer?: AgentLayer;
      role?: AgentRole;
      domain?: string | null;
      parent_id?: string | null;
      model_profile?: string;
      auth_source?: AgentAuthSource;
      api_key_var?: string | null;
      model_name?: string | null;
      enabled?: boolean;
      clear_api_key?: boolean;
      clear_model_name?: boolean;
      clear_parent?: boolean;
      clear_domain?: boolean;
    }
  ) =>
    request<AgentRosterConfig>(
      `/api/settings/agents/${encodeURIComponent(agentId)}`,
      { method: "PUT", body: JSON.stringify(payload) },
      () => {
        mockStore.roster = {
          agents: mockStore.roster.agents.map((slot) => {
            if (slot.id !== agentId) return slot;
            const nextAuth: AgentAuthSource =
              payload.auth_source ??
              (payload.clear_api_key
                ? "profile_default"
                : payload.api_key_var
                  ? "api_key"
                  : slot.auth_source);
            const nextKeyVar =
              nextAuth === "api_key"
                ? payload.clear_api_key
                  ? null
                  : payload.api_key_var !== undefined
                    ? payload.api_key_var
                    : slot.api_key_var
                : null;
            return {
              ...slot,
              ...(payload.name !== undefined ? { name: payload.name } : {}),
              ...(payload.layer !== undefined ? { layer: payload.layer } : {}),
              ...(payload.role !== undefined ? { role: payload.role } : {}),
              ...(payload.clear_domain
                ? { domain: null }
                : payload.domain !== undefined
                  ? { domain: payload.domain }
                  : {}),
              ...(payload.clear_parent
                ? { parent_id: null }
                : payload.parent_id !== undefined
                  ? { parent_id: payload.parent_id }
                  : {}),
              ...(payload.model_profile !== undefined
                ? { model_profile: normalizeProfileKey(payload.model_profile) }
                : {}),
              auth_source: nextAuth,
              api_key_var: nextKeyVar,
              ...(payload.clear_model_name
                ? { model_name: null }
                : payload.model_name !== undefined
                  ? { model_name: payload.model_name }
                  : {}),
              ...(payload.enabled !== undefined ? { enabled: payload.enabled } : {}),
            };
          }),
        };
        return mockStore.roster;
      }
    ),

  deleteAgentSlot: (agentId: string) =>
    request<AgentRosterConfig>(
      `/api/settings/agents/${encodeURIComponent(agentId)}`,
      { method: "DELETE" },
      () => {
        mockStore.roster = {
          agents: mockStore.roster.agents
            .filter((slot) => slot.id !== agentId)
            .map((slot) =>
              slot.parent_id === agentId ? { ...slot, parent_id: null } : slot
            ),
        };
        return mockStore.roster;
      }
    ),

  getPreviewUrl: (runId: string, env: "local" | "staging" | "production" = "local") =>
    request<PreviewUrlResponse>(
      `/api/preview/${runId}/url?env=${env}`,
      undefined,
      () =>
        mockStore.previews[runId]?.[env] ?? {
          run_id: runId,
          env,
          url: null,
          available: false,
        }
    ),
};

export class RunEventStream {
  private ws: WebSocket | null = null;
  private lastSeq = 0;
  private closed = false;
  private reconnectTimer: number | null = null;
  private mockUnsub: (() => void) | null = null;

  constructor(
    private runId: string,
    private onEvent: (ev: BlackboardEvent) => void,
    private onStatus?: (connected: boolean) => void
  ) {}

  start() {
    this.closed = false;
    if (isDemoMode()) {
      this.startMockStream();
      return;
    }
    this.connect();
  }

  stop() {
    this.closed = true;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.mockUnsub) {
      this.mockUnsub();
      this.mockUnsub = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private startMockStream() {
    const initial = mockStore.events[this.runId] ?? [];
    for (const ev of initial) {
      if (ev.publish_seq && ev.publish_seq > this.lastSeq) {
        this.lastSeq = ev.publish_seq;
      }
      this.onEvent(structuredClone(ev));
    }
    this.onStatus?.(true);

    this.mockUnsub = mockStore.subscribe(this.runId, (ev) => {
      if (this.closed) return;
      if (ev.publish_seq && ev.publish_seq > this.lastSeq) {
        this.lastSeq = ev.publish_seq;
      }
      this.onEvent(structuredClone(ev));
    });
  }

  private connect() {
    if (this.closed) return;
    if (isDemoMode()) {
      this.startMockStream();
      return;
    }
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws/runs/${this.runId}?after_seq=${this.lastSeq}`;
    try {
      const ws = new WebSocket(url);
      this.ws = ws;
      ws.onopen = () => this.onStatus?.(true);
      ws.onmessage = (msg) => {
        try {
          const ev = JSON.parse(msg.data) as BlackboardEvent & { type?: string };
          if (ev.type === "pong" || ev.run_id === undefined) return;
          if (ev.publish_seq && ev.publish_seq > this.lastSeq) {
            this.lastSeq = ev.publish_seq;
          }
          this.onEvent(ev);
        } catch {
          // ignore malformed frames
        }
      };
      ws.onclose = () => {
        this.onStatus?.(false);
        if (!this.closed) {
          this.reconnectTimer = window.setTimeout(() => this.connect(), 2500);
        }
      };
      ws.onerror = () => {
        ws.close();
      };
    } catch {
      this.onStatus?.(false);
      this.reconnectTimer = window.setTimeout(() => this.connect(), 2500);
    }
  }
}
