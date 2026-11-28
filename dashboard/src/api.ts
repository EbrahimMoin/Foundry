import {
  INITIAL_MOCK_AGENTS,
  INITIAL_MOCK_AUTONOMY,
  INITIAL_MOCK_CONTRACTS,
  INITIAL_MOCK_COSTS,
  INITIAL_MOCK_DATA_POLICY,
  INITIAL_MOCK_DECISIONS,
  INITIAL_MOCK_EVENTS,
  INITIAL_MOCK_METRICS,
  INITIAL_MOCK_MODELS,
  INITIAL_MOCK_NOTES,
  INITIAL_MOCK_PREVIEWS,
  INITIAL_MOCK_RESERVATIONS,
  INITIAL_MOCK_RUNS,
  INITIAL_MOCK_RUNTIME_SETTINGS,
  MOCK_CONTRACT_VERSIONS,
  MOCK_PROJECTS,
  MOCK_TASK_WRITE_SETS,
  INITIAL_MOCK_TASKS,
  PRIMARY_RUN_ID,
} from "./mockData";
import type {
  AgentRecord,
  AutonomyConfig,
  AutonomyOverrides,
  BlackboardEvent,
  ContractRecord,
  ContractVersionsResponse,
  CostsSummaryResponse,
  DataPolicyConfig,
  DecisionRecord,
  MetricsSummaryResponse,
  ModelsConfig,
  NoteRecord,
  PreviewUrlResponse,
  ProjectSummary,
  RunRecord,
  RuntimeSettingsResponse,
  TaskDetailResponse,
  TaskRecord,
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
  costs: Record<string, CostsSummaryResponse> = structuredClone(INITIAL_MOCK_COSTS);
  metrics: Record<string, MetricsSummaryResponse> = structuredClone(INITIAL_MOCK_METRICS);
  models: ModelsConfig = structuredClone(INITIAL_MOCK_MODELS);
  autonomy: AutonomyConfig = structuredClone(INITIAL_MOCK_AUTONOMY);
  dataPolicy: DataPolicyConfig = structuredClone(INITIAL_MOCK_DATA_POLICY);
  runtimeSettings: RuntimeSettingsResponse = structuredClone(INITIAL_MOCK_RUNTIME_SETTINGS);
  events: Record<string, BlackboardEvent[]> = structuredClone(INITIAL_MOCK_EVENTS);
  previews = structuredClone(INITIAL_MOCK_PREVIEWS);

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
