import type {
  AgentRecord,
  BlackboardEvent,
  CostsSummaryResponse,
  ModelProfile,
  ModelsConfig,
  RunRecord,
  TaskRecord,
} from "../types";

export type GateId = "G1" | "G2" | "G3" | "G4" | "G5";
export type GateState = "passed" | "active" | "failed" | "pending";

export interface GateStatus {
  id: GateId;
  label: string;
  state: GateState;
}

export interface WaveProgress {
  wave: number;
  label: string;
  total: number;
  merged: number;
  inFlight: number;
  status: "merged" | "running" | "queued";
}

export interface VerifierDiversityAudit {
  diverse: boolean;
  implementerProfile: string;
  implementerFamily: string;
  verifierProfiles: Array<{ role: string; profile: string; family: string }>;
  sameFamilyRoles: string[];
}

export type EventCategory = "all" | "task" | "contract" | "verify" | "decision" | "system";

const GATE_DEFINITIONS: Array<{ id: GateId; label: string }> = [
  { id: "G1", label: "Lint & Type" },
  { id: "G2", label: "Unit & Contract" },
  { id: "G3", label: "Black-Box Tests" },
  { id: "G4", label: "Cross-Family Review" },
  { id: "G5", label: "Main Merge" },
];

/** Ensures profile keys always carry a single leading `@` (matches models.yaml). */
export function normalizeProfileKey(key: string): string {
  const cleaned = key.trim().replace(/^@+/, "");
  return `@${cleaned}`;
}

export function lookupModelProfile(
  models: ModelsConfig | null | undefined,
  profileRef: string
): ModelProfile | undefined {
  if (!models || !profileRef) return undefined;
  const normalized = normalizeProfileKey(profileRef);
  const bare = normalized.slice(1);
  return models.profiles[normalized] ?? models.profiles[profileRef] ?? models.profiles[bare];
}

export function deriveTaskGates(task: Pick<TaskRecord, "status" | "attempt">): GateStatus[] {
  const status = task.status.toUpperCase();
  const statesByStatus: Record<string, GateState[]> = {
    PENDING: ["pending", "pending", "pending", "pending", "pending"],
    READY: ["pending", "pending", "pending", "pending", "pending"],
    IN_PROGRESS: ["passed", "active", "pending", "pending", "pending"],
    IN_REVIEW: ["passed", "passed", "active", "active", "pending"],
    VERIFIED: ["passed", "passed", "passed", "passed", "pending"],
    QUEUED: ["passed", "passed", "passed", "passed", "active"],
    MERGED: ["passed", "passed", "passed", "passed", "passed"],
    ESCALATED: ["passed", "passed", "failed", "pending", "pending"],
    FAILED: ["passed", "passed", "failed", "pending", "pending"],
  };
  const states = statesByStatus[status] ?? statesByStatus.READY;
  return GATE_DEFINITIONS.map((gate, idx) => ({
    ...gate,
    state: states[idx],
  }));
}

export function computeWaveProgress(tasks: TaskRecord[]): WaveProgress[] {
  const waves = new Map<number, TaskRecord[]>();
  for (const t of tasks) {
    const list = waves.get(t.wave) ?? [];
    list.push(t);
    waves.set(t.wave, list);
  }
  const waveNumbers = Array.from(new Set(waves.keys())).sort((a, b) => a - b);
  return waveNumbers.map((wave) => {
    const items = waves.get(wave) ?? [];
    const merged = items.filter((t) => t.status === "MERGED").length;
    const inFlight = items.filter((t) =>
      ["IN_PROGRESS", "IN_REVIEW", "VERIFIED", "QUEUED", "ESCALATED"].includes(t.status)
    ).length;
    const status: WaveProgress["status"] =
      items.length > 0 && merged === items.length
        ? "merged"
        : inFlight > 0 || merged > 0
          ? "running"
          : "queued";
    const label = wave === 1 ? "Foundation" : wave === 2 ? "Features" : `Wave ${wave}`;
    return { wave, label, total: items.length, merged, inFlight, status };
  });
}

export function resolveProfileFamily(models: ModelsConfig, profileRef: string): string {
  const profile = lookupModelProfile(models, profileRef);
  if (!profile) return "unknown";
  if (profile.family) return profile.family.toLowerCase();
  const provider = models.providers[profile.provider];
  if (provider?.family) return provider.family.toLowerCase();
  return profile.provider.split("_")[0].toLowerCase();
}

export function checkVerifierDiversity(models: ModelsConfig): VerifierDiversityAudit {
  const roles = models.assignments.roles;
  const implementerProfile = normalizeProfileKey(
    roles.implementer ?? models.assignments.project_default
  );
  const implementerFamily = resolveProfileFamily(models, implementerProfile);

  const verifierRoles = ["test_writer", "reviewer"];
  const verifierProfiles = verifierRoles.map((role) => {
    const profile = normalizeProfileKey(roles[role] ?? models.assignments.global_default);
    const family = resolveProfileFamily(models, profile);
    return { role, profile, family };
  });

  const sameFamilyRoles = verifierProfiles
    .filter((v) => v.family !== "unknown" && v.family === implementerFamily)
    .map((v) => v.role);

  return {
    diverse: sameFamilyRoles.length === 0,
    implementerProfile,
    implementerFamily,
    verifierProfiles,
    sameFamilyRoles,
  };
}

export function classifyEventCategory(eventType: string): Exclude<EventCategory, "all"> {
  if (eventType.startsWith("task.") || eventType.startsWith("reservation.")) return "task";
  if (eventType.startsWith("contract.")) return "contract";
  if (
    eventType.startsWith("verify.") ||
    eventType.startsWith("gate.") ||
    eventType.startsWith("dispute.")
  ) {
    return "verify";
  }
  if (eventType.startsWith("decision.")) return "decision";
  return "system";
}

export function filterAndSortEvents(
  events: BlackboardEvent[],
  category: EventCategory,
  searchQuery: string
): BlackboardEvent[] {
  const q = searchQuery.trim().toLowerCase();
  return [...events]
    .sort((a, b) => {
      const seqA = a.publish_seq ?? a.id;
      const seqB = b.publish_seq ?? b.id;
      return seqB - seqA;
    })
    .filter((ev) => {
      if (category !== "all" && classifyEventCategory(ev.type) !== category) {
        return false;
      }
      if (!q) return true;
      const haystack = `${ev.type} ${ev.subject_id ?? ""} ${ev.publish_seq ?? ""} ${JSON.stringify(ev.payload)}`.toLowerCase();
      return haystack.includes(q);
    });
}

export function filterTasks(
  tasks: TaskRecord[],
  opts: { wave?: number | "all"; domain?: string | "all"; query?: string }
): TaskRecord[] {
  const q = (opts.query ?? "").trim().toLowerCase();
  return tasks.filter((t) => {
    if (opts.wave !== undefined && opts.wave !== "all" && t.wave !== opts.wave) {
      return false;
    }
    if (opts.domain && opts.domain !== "all" && t.domain !== opts.domain) {
      return false;
    }
    if (!q) return true;
    return (
      t.title.toLowerCase().includes(q) ||
      t.id.toLowerCase().includes(q) ||
      t.domain.toLowerCase().includes(q) ||
      (t.feature ?? "").toLowerCase().includes(q) ||
      (t.branch ?? "").toLowerCase().includes(q)
    );
  });
}

export function computeBudgetMetrics(costs: CostsSummaryResponse | null): {
  utilizationPct: number;
  remainingUsd: number;
  cachedTokensTotal: number;
  inputTokensTotal: number;
  outputTokensTotal: number;
  estCacheSavingsUsd: number;
} {
  if (!costs) {
    return {
      utilizationPct: 0,
      remainingUsd: 0,
      cachedTokensTotal: 0,
      inputTokensTotal: 0,
      outputTokensTotal: 0,
      estCacheSavingsUsd: 0,
    };
  }
  const utilizationPct =
    costs.budget_usd > 0 ? Math.min(100, (costs.spent_usd / costs.budget_usd) * 100) : 0;
  const remainingUsd = Math.max(0, costs.budget_usd - costs.spent_usd);
  let cachedTokensTotal = 0;
  let inputTokensTotal = 0;
  let outputTokensTotal = 0;
  for (const bucket of costs.by_model) {
    cachedTokensTotal += bucket.cached_tokens;
    inputTokensTotal += bucket.input_tokens;
    outputTokensTotal += bucket.output_tokens;
  }
  const estCacheSavingsUsd = Math.max(0, costs.total_est_value_usd - costs.total_call_cost_usd);
  return {
    utilizationPct,
    remainingUsd,
    cachedTokensTotal,
    inputTokensTotal,
    outputTokensTotal,
    estCacheSavingsUsd,
  };
}

export type AgentTier = "orchestrator" | "leads" | "implementers" | "verifiers";

export function groupAgentsByHierarchy(agents: AgentRecord[]): Record<AgentTier, AgentRecord[]> {
  const groups: Record<AgentTier, AgentRecord[]> = {
    orchestrator: [],
    leads: [],
    implementers: [],
    verifiers: [],
  };
  for (const agent of agents) {
    if (agent.role === "orchestrator") {
      groups.orchestrator.push(agent);
    } else if (agent.role === "lead") {
      groups.leads.push(agent);
    } else if (agent.role === "test_writer" || agent.role === "reviewer") {
      groups.verifiers.push(agent);
    } else {
      groups.implementers.push(agent);
    }
  }
  return groups;
}

export function shortId(id: string | null | undefined, length = 8): string {
  if (!id) return "—";
  return id.length <= length ? id : id.slice(0, length);
}

export function formatShortTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  return dt.toTimeString().slice(0, 5);
}

export function summarizeRunLabel(run: RunRecord, index: number): string {
  const phase = run.phase.replace(/_/g, " ");
  if (run.project_id) {
    return `${run.project_id} · ${phase}`;
  }
  return `Run #${index + 1} · ${phase}`;
}
