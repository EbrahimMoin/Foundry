export interface RunRecord {
  id: string;
  project_id: string;
  phase: string;
  budget_usd: number;
  spent_usd: number;
  data_policy: Record<string, boolean>;
  started_at: string;
  ended_at: string | null;
}

export interface ProjectSummary {
  id: string;
  name: string;
  stack_pack: string;
  run_count: number;
  latest_run_id: string | null;
  latest_phase: string | null;
}

export interface AgentRecord {
  id: string;
  run_id: string;
  role: string;
  domain: string | null;
  parent_agent_id: string | null;
  model_profile: string;
  status: string;
  graph_thread_id: string | null;
}

export interface TaskRecord {
  id: string;
  run_id: string;
  wave: number;
  domain: string;
  feature: string | null;
  kind: string;
  title: string;
  description: string;
  acceptance: unknown[];
  status: string;
  owner_agent_id: string | null;
  parent_task_id?: string | null;
  attempt: number;
  max_attempts: number;
  contract_reopens: number;
  base_sha?: string | null;
  branch: string | null;
  worktree_path?: string | null;
  graph_thread_id: string | null;
  pending_deps?: string[];
  budget_implement_usd?: number;
  budget_review_usd?: number;
  spent_implement_usd: number;
  spent_review_usd: number;
  version: number;
  created_at?: string;
  updated_at: string;
}

export interface WriteReservationRecord {
  id: number;
  run_id: string;
  task_id: string;
  path: string;
  expires_at: string;
  heartbeat_at: string;
  released_at: string | null;
}

export interface TaskDetailResponse {
  task: TaskRecord;
  write_set: string[];
  dependencies: string[];
  reservations: WriteReservationRecord[];
}

export interface DecisionRecord {
  id: string;
  run_id: string;
  idempotency_key: string;
  category: string;
  title: string;
  context: string;
  options: string[];
  recommendation: unknown;
  confidence: number | null;
  reversibility: string | null;
  impact: Record<string, unknown>;
  mode: string;
  status: string;
  answer: { action?: string; choice?: unknown; feedback?: string | null } | null;
  decided_by: string | null;
  graph_thread_id?: string | null;
  blocks_task_ids?: string[];
  version?: number;
  created_at: string;
  decided_at: string | null;
}

export interface ContractRecord {
  id: string;
  run_id: string;
  name: string;
  domain: string;
  kind: string;
  owner_agent_id?: string | null;
  current_version: number;
  status: string;
  breaking_in_wave: number;
  version?: number;
}

export interface ContractVersionRecord {
  contract_id: string;
  version: number;
  body: Record<string, unknown>;
  pinned_pointers: string[];
  change_kind: string;
  changed_elements: string[];
  change_summary: string | null;
  author_agent_id: string | null;
  approved_by: string | null;
  created_at: string;
}

export interface ContractVersionsResponse {
  contract_id: string;
  name: string;
  domain: string;
  kind: string;
  status: string;
  current_version: number;
  versions: ContractVersionRecord[];
}

export interface NoteRecord {
  id: string;
  run_id: string;
  task_id: string | null;
  feature: string | null;
  kind: "note" | "gotcha" | string;
  body: string;
  created_at: string;
}

export interface ModelCostBucket {
  key: string;
  calls: number;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  cost_usd: number;
  est_value_usd: number;
}

export interface LlmCallRecord {
  id: number;
  run_id: string | null;
  agent_id: string | null;
  task_id: string | null;
  provider: string;
  model: string;
  profile: string;
  transport: string;
  billing_mode: string;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  cost_usd: number;
  est_value_usd: number;
  latency_ms: number | null;
  replayed: boolean;
  created_at: string;
}

export interface CostsSummaryResponse {
  run_id: string;
  budget_usd: number;
  spent_usd: number;
  total_call_cost_usd: number;
  total_est_value_usd: number;
  call_count: number;
  cache_hit_rate: number;
  by_model: ModelCostBucket[];
  by_profile: ModelCostBucket[];
  calls?: LlmCallRecord[];
}

export interface FeatureMetricsSummary {
  name: string;
  status: string;
  budget_blackbox_tests_usd: number;
  spent_blackbox_tests_usd: number;
  dispute_rounds: number;
}

export interface MetricsSummaryResponse {
  run_id: string;
  phase: string;
  tasks_total: number;
  tasks_by_status: Record<string, number>;
  total_attempts: number;
  total_contract_reopens: number;
  active_reservations: number;
  contracts_count: number;
  pending_decisions: number;
  dispute_rounds_total: number;
  features: FeatureMetricsSummary[];
  events_count: number;
}

export interface ProviderConfig {
  transport: string;
  billing_mode: string;
  base_url: string | null;
  family: string | null;
  data_tier: string;
}

export interface ModelProfile {
  provider: string;
  model: string;
  family: string | null;
}

export interface ModelsConfig {
  providers: Record<string, ProviderConfig>;
  profiles: Record<string, ModelProfile>;
  assignments: {
    roles: Record<string, string>;
    project_default: string;
    global_default: string;
  };
}

export type DecisionModeOption = "auto" | "approve" | "human";

export interface AutonomyOverrides {
  strategic: DecisionModeOption | null;
  tactical: DecisionModeOption | null;
  operational: DecisionModeOption | null;
  release_infra: DecisionModeOption | null;
}

export interface AutonomyConfig {
  preset: "autopilot" | "balanced" | "architect" | "full_control" | "benchmark";
  overrides: AutonomyOverrides;
  confidence_threshold: number;
  decision_timeout_minutes?: number | null;
}

export interface DataPolicyConfig {
  allow_free_tier: boolean;
  allow_trace_export: boolean;
}

export interface RuntimeSettingsResponse {
  server: { host: string; port: number };
  runtime: {
    max_heavy_exec_slots: number;
    max_concurrent_tasks: number;
    sandbox_limits: {
      memory: string;
      cpus: number;
      pids: number;
      node_max_old_space_mb: number;
    };
    sandbox_network: string;
    egress_proxy_allowlist: string[];
  };
  embeddings: {
    provider: string;
    model: string;
  };
  observability: {
    local_tracing: boolean;
    phoenix: boolean;
    langsmith_export: boolean;
  };
  budgets: {
    monthly_usd: number;
  };
  secrets_backends: Array<"keyring" | "encrypted_vault">;
}

export interface PreviewUrlResponse {
  run_id: string;
  env: "local" | "staging" | "production";
  url: string | null;
  available: boolean;
}

export interface BlackboardEvent {
  id: number;
  publish_seq: number | null;
  run_id: string;
  type: string;
  subject_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  published_at: string | null;
}
