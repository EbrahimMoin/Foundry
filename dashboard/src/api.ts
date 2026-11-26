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

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as T;
}

export const api = {
  listProjects: () => requestJson<ProjectSummary[]>("/api/projects"),
  listRuns: () => requestJson<RunRecord[]>("/api/runs"),
  pauseRun: (runId: string) => requestJson(`/api/runs/${runId}/pause`, { method: "POST" }),
  resumeRun: (runId: string) => requestJson(`/api/runs/${runId}/resume`, { method: "POST" }),
  stopRun: (runId: string) => requestJson(`/api/runs/${runId}/stop`, { method: "POST" }),
  listAgents: (runId: string) => requestJson<AgentRecord[]>(`/api/runs/${runId}/agents`),
  listTasks: (runId: string) => requestJson<TaskRecord[]>(`/api/runs/${runId}/tasks`),
  listReservations: (runId: string) =>
    requestJson<WriteReservationRecord[]>(`/api/runs/${runId}/reservations`),
  listContracts: (runId: string) => requestJson<ContractRecord[]>(`/api/runs/${runId}/contracts`),
  listDecisions: (runId: string) => requestJson<DecisionRecord[]>(`/api/runs/${runId}/decisions`),
  resolveDecision: (
    decisionId: string,
    body: { action: "approve" | "reject"; choice?: unknown; feedback?: string }
  ) =>
    requestJson<DecisionRecord>(`/api/decisions/${decisionId}/resolve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getCosts: (runId: string) => requestJson<CostsSummaryResponse>(`/api/runs/${runId}/costs`),
  getMetrics: (runId: string) => requestJson<MetricsSummaryResponse>(`/api/runs/${runId}/metrics`),
  getModels: () => requestJson<ModelsConfig>("/api/settings/models"),
  updateModelAssignments: (roles: Record<string, string>) =>
    requestJson<ModelsConfig>("/api/settings/models/assignments", {
      method: "PUT",
      body: JSON.stringify({ roles }),
    }),
  getAutonomy: () => requestJson<AutonomyConfig>("/api/settings/autonomy"),
  updateAutonomy: (preset: AutonomyConfig["preset"]) =>
    requestJson<AutonomyConfig>("/api/settings/autonomy", {
      method: "PUT",
      body: JSON.stringify({ preset }),
    }),
  getDataPolicy: () => requestJson<DataPolicyConfig>("/api/settings/data-policy"),
  updateDataPolicy: (policy: DataPolicyConfig) =>
    requestJson<DataPolicyConfig>("/api/settings/data-policy", {
      method: "PUT",
      body: JSON.stringify(policy),
    }),
  getPreviewUrl: (runId: string, env: "local" | "staging" | "production") =>
    requestJson<PreviewUrlResponse>(`/api/preview/${runId}/url?env=${env}`),
};

export class RunEventStream {
  private ws: WebSocket | null = null;
  private lastSeq = 0;
  private stopped = false;
  private reconnectTimer: number | null = null;

  constructor(
    private readonly runId: string,
    private readonly onEvent: (ev: BlackboardEvent) => void,
    private readonly onStatus: (connected: boolean, lastSeq: number) => void
  ) {}

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }

  private connect(): void {
    if (this.stopped) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/ws/runs/${this.runId}?after_seq=${this.lastSeq}`;
    const socket = new WebSocket(url);
    this.ws = socket;

    socket.onopen = () => {
      this.onStatus(true, this.lastSeq);
    };

    socket.onmessage = (msg) => {
      const parsed = JSON.parse(String(msg.data)) as BlackboardEvent | { type: "pong" };
      if ("publish_seq" in parsed && typeof parsed.publish_seq === "number") {
        if (parsed.publish_seq > this.lastSeq) {
          this.lastSeq = parsed.publish_seq;
          this.onEvent(parsed);
          this.onStatus(true, this.lastSeq);
        }
      }
    };

    socket.onclose = () => {
      this.onStatus(false, this.lastSeq);
      if (!this.stopped) {
        this.reconnectTimer = window.setTimeout(() => this.connect(), 1500);
      }
    };
  }
}
