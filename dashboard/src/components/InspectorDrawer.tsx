import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import { AgentLayersStudio } from "./AgentLayersStudio";
import { ApiKeysPanel } from "./ApiKeysPanel";
import { CostsMetricsPanel } from "./CostsMetricsPanel";
import { DecisionsPanel } from "./DecisionsPanel";
import { ModelSwitcherPanel } from "./ModelSwitcherPanel";
import type { InspectorSection } from "./CommandDock";
import type {
  AgentAuthSource,
  AgentLayer,
  AgentRecord,
  AgentRole,
  AgentRosterConfig,
  ApiKeysStatusResponse,
  BlackboardEvent,
  CostsSummaryResponse,
  DataPolicyConfig,
  DecisionRecord,
  KeyModelsResponse,
  MetricsSummaryResponse,
  ModelProfileUpsertPayload,
  ModelsConfig,
  OpenAISubscriptionStatus,
  ProviderCatalogEntry,
  TestConnectionResponse,
} from "../types";
import {
  classifyEventCategory,
  filterAndSortEvents,
  type EventCategory,
} from "../utils/foundryMetrics";

interface InspectorDrawerProps {
  section: InspectorSection;
  onChangeSection: (section: InspectorSection) => void;
  onClose: () => void;
  activeRunId: string | null;
  runAgents: AgentRecord[];
  roster: AgentRosterConfig | null;
  apiKeys: ApiKeysStatusResponse | null;
  catalog: ProviderCatalogEntry[];
  openAiSub: OpenAISubscriptionStatus | null;
  keyModelsMap: Record<string, string[]>;
  onCreateAgentSlot: (payload: {
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
  }) => Promise<void>;
  onUpdateAgentSlot: (
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
  ) => Promise<void>;
  onDeleteAgentSlot: (agentId: string) => Promise<void>;
  onAddRunAgent: (
    runId: string,
    payload: {
      role: AgentRole;
      domain?: string | null;
      parent_agent_id?: string | null;
      model_profile: string;
    }
  ) => Promise<void>;
  onRemoveRunAgent: (runId: string, agentId: string) => Promise<void>;
  onUnlockVault: (passphrase: string) => Promise<void>;
  onSaveApiKey: (payload: {
    name: string;
    secret: string;
    provider: string;
    passphrase?: string;
  }) => Promise<void>;
  onDeleteApiKey: (name: string) => Promise<void>;
  onFetchKeyModels: (name: string) => Promise<KeyModelsResponse>;
  onToggleOpenAiSub: (enabled: boolean) => Promise<void>;
  onTestConnection: (payload: {
    auth_source: "api_key" | "openai_subscription";
    api_key_var?: string | null;
    model: string;
  }) => Promise<TestConnectionResponse>;
  decisions: DecisionRecord[];
  onResolveDecision: (
    decisionId: string,
    body: { action: "approve" | "reject"; choice?: unknown; feedback?: string }
  ) => Promise<void>;
  models: ModelsConfig | null;
  dataPolicy: DataPolicyConfig | null;
  onUpdateRole: (role: string, profile: string) => Promise<void>;
  onUpsertProfile: (payload: ModelProfileUpsertPayload) => Promise<void>;
  onDeleteProfile: (profileName: string) => Promise<void>;
  onUpdateDataPolicy: (patch: Partial<DataPolicyConfig>) => Promise<void>;
  costs: CostsSummaryResponse | null;
  metrics: MetricsSummaryResponse | null;
  events: BlackboardEvent[];
}

const SECTION_TITLES: Record<InspectorSection, string> = {
  agents: "Agents",
  keys: "API Keys & CLI",
  models: "Models & Policy",
  decisions: "Decisions",
  costs: "Costs",
  outbox: "Activity Outbox",
};

function eventColor(type: string): string {
  switch (classifyEventCategory(type)) {
    case "task":
      return "var(--status-active)";
    case "contract":
      return "var(--status-violet)";
    case "verify":
      return "var(--status-nominal)";
    case "decision":
      return "var(--accent)";
    default:
      return "var(--ink-secondary)";
  }
}

export function InspectorDrawer({
  section,
  onChangeSection,
  onClose,
  activeRunId,
  runAgents,
  roster,
  apiKeys,
  catalog,
  openAiSub,
  keyModelsMap,
  onCreateAgentSlot,
  onUpdateAgentSlot,
  onDeleteAgentSlot,
  onAddRunAgent,
  onRemoveRunAgent,
  onUnlockVault,
  onSaveApiKey,
  onDeleteApiKey,
  onFetchKeyModels,
  onToggleOpenAiSub,
  onTestConnection,
  decisions,
  onResolveDecision,
  models,
  dataPolicy,
  onUpdateRole,
  onUpsertProfile,
  onDeleteProfile,
  onUpdateDataPolicy,
  costs,
  metrics,
  events,
}: InspectorDrawerProps) {
  const [category, setCategory] = useState<EventCategory>("all");
  const [expandedEventKey, setExpandedEventKey] = useState<string | null>(null);

  const pendingCount = decisions.filter((d) => d.status === "pending").length;
  const enabledAgentCount = (roster?.agents ?? []).filter((a) => a.enabled).length;
  const savedKeysCount = apiKeys?.keys.length ?? 0;

  const filteredEvents = useMemo(
    () => filterAndSortEvents(events, category, ""),
    [events, category]
  );

  const tabs: Array<{ key: InspectorSection; label: string }> = [
    { key: "agents", label: enabledAgentCount > 0 ? `Agents (${enabledAgentCount})` : "Agents" },
    { key: "keys", label: savedKeysCount > 0 ? `API Keys (${savedKeysCount})` : "API Keys" },
    { key: "models", label: "Models" },
    {
      key: "decisions",
      label: pendingCount > 0 ? `Decisions (${pendingCount})` : "Decisions",
    },
    { key: "costs", label: "Costs" },
    { key: "outbox", label: "Outbox" },
  ];

  return (
    <aside
      aria-label="Control workbench sidebar"
      className="w-full lg:w-[500px] xl:w-[560px] shrink-0 border-t lg:border-t-0 lg:border-l flex flex-col h-full min-h-0"
      style={{
        backgroundColor: "var(--bg-canvas)",
        borderColor: "var(--border-hairline)",
      }}
    >
      <div className="px-5 pt-5 pb-3 shrink-0 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-serif text-[25px] font-medium tracking-tight text-[var(--ink-primary)]">
            {SECTION_TITLES[section]}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close inspector"
            className="w-8 h-8 rounded-full flex items-center justify-center transition-colors hover:opacity-80"
            style={{
              backgroundColor: "var(--bg-active)",
              color: "var(--ink-primary)",
            }}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div
          className="flex flex-wrap items-center gap-1.5"
          role="tablist"
          aria-label="Right sidebar sections"
        >
          {tabs.map((tab) => {
            const active = section === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => onChangeSection(tab.key)}
                className="h-7 px-2.5 rounded-full border text-[12px] transition-colors"
                style={
                  active
                    ? {
                        backgroundColor: "var(--bg-active)",
                        borderColor: "transparent",
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
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden px-1 pb-4" role="tabpanel">
        {section === "agents" && (
          <AgentLayersStudio
            compact
            roster={roster}
            models={models}
            apiKeys={apiKeys}
            catalog={catalog}
            openAiSub={openAiSub}
            keyModelsMap={keyModelsMap}
            activeRunId={activeRunId}
            runAgents={runAgents}
            onCreateAgentSlot={onCreateAgentSlot}
            onUpdateAgentSlot={onUpdateAgentSlot}
            onDeleteAgentSlot={onDeleteAgentSlot}
            onAddRunAgent={onAddRunAgent}
            onRemoveRunAgent={onRemoveRunAgent}
            onOpenApiKeys={() => onChangeSection("keys")}
          />
        )}

        {section === "keys" && (
          <ApiKeysPanel
            apiKeys={apiKeys}
            catalog={catalog}
            openAiSub={openAiSub}
            onUnlockVault={onUnlockVault}
            onSaveApiKey={onSaveApiKey}
            onDeleteApiKey={onDeleteApiKey}
            onFetchKeyModels={onFetchKeyModels}
            onToggleOpenAiSub={onToggleOpenAiSub}
            onTestConnection={onTestConnection}
            onOpenModels={() => onChangeSection("models")}
          />
        )}

        {section === "models" && (
          <ModelSwitcherPanel
            models={models}
            dataPolicy={dataPolicy}
            apiKeys={apiKeys}
            catalog={catalog}
            openAiSub={openAiSub}
            keyModelsMap={keyModelsMap}
            onUpdateRole={onUpdateRole}
            onUpsertProfile={onUpsertProfile}
            onDeleteProfile={onDeleteProfile}
            onFetchKeyModels={onFetchKeyModels}
            onUpdateDataPolicy={onUpdateDataPolicy}
            onOpenApiKeys={() => onChangeSection("keys")}
          />
        )}

        {section === "decisions" && (
          <DecisionsPanel decisions={decisions} onResolve={onResolveDecision} />
        )}

        {section === "costs" && <CostsMetricsPanel costs={costs} metrics={metrics} />}

        {section === "outbox" && (
          <div className="flex flex-col h-full min-h-0 px-4 pt-2">
            <div className="pb-3 flex items-center gap-1.5 overflow-x-auto shrink-0">
              {(["all", "task", "contract", "verify", "decision"] as const).map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setCategory(cat)}
                  className="px-2.5 py-1 rounded-full text-[11.5px] capitalize transition-colors"
                  style={
                    category === cat
                      ? { backgroundColor: "var(--bg-active)", color: "var(--ink-primary)" }
                      : { color: "var(--ink-muted)" }
                  }
                >
                  {cat}
                </button>
              ))}
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto space-y-2 pb-4">
              {filteredEvents.length === 0 ? (
                <div className="py-10 text-center text-[12.5px] text-[var(--ink-muted)]">
                  No outbox events match this filter.
                </div>
              ) : (
                filteredEvents.map((ev) => {
                  const key = `${ev.publish_seq ?? ev.id}-${ev.type}`;
                  const expanded = expandedEventKey === key;
                  return (
                    <div
                      key={key}
                      className="rounded-xl border overflow-hidden"
                      style={{
                        backgroundColor: "var(--bg-surface)",
                        borderColor: "var(--border-hairline)",
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => setExpandedEventKey(expanded ? null : key)}
                        className="w-full text-left px-3.5 py-2.5 flex items-start gap-2 hover:bg-[var(--bg-hover)] font-mono text-[11.5px]"
                      >
                        <span className="mt-0.5 text-[var(--ink-muted)] shrink-0">
                          {expanded ? (
                            <ChevronDown className="w-3.5 h-3.5" />
                          ) : (
                            <ChevronRight className="w-3.5 h-3.5" />
                          )}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <span
                              style={{ color: eventColor(ev.type) }}
                              className="font-medium truncate"
                            >
                              {ev.type}
                            </span>
                            <span className="text-[var(--ink-muted)] tabular shrink-0">
                              #{ev.publish_seq ?? ev.id}
                            </span>
                          </div>
                          {ev.subject_id && (
                            <div className="text-[10.5px] text-[var(--ink-muted)] truncate mt-0.5">
                              {ev.subject_id}
                            </div>
                          )}
                        </div>
                      </button>

                      {expanded && (
                        <div className="px-3.5 pb-3">
                          <pre
                            className="rounded-lg border p-2.5 font-mono text-[10.5px] leading-relaxed overflow-x-auto text-[var(--ink-primary)]"
                            style={{
                              backgroundColor: "var(--bg-canvas)",
                              borderColor: "var(--border-hairline)",
                            }}
                          >
                            {JSON.stringify(ev.payload, null, 2)}
                          </pre>
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
