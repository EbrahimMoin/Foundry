import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  KeyRound,
  Layers,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import type {
  AgentAuthSource,
  AgentLayer,
  AgentRecord,
  AgentRole,
  AgentRosterConfig,
  AgentSlotConfig,
  ApiKeysStatusResponse,
  ModelsConfig,
  OpenAISubscriptionStatus,
  ProviderCatalogEntry,
} from "../types";
import {
  checkVerifierDiversity,
  normalizeProfileKey,
  resolveProfileFamily,
} from "../utils/foundryMetrics";

interface AgentLayersStudioProps {
  compact?: boolean;
  roster: AgentRosterConfig | null;
  models: ModelsConfig | null;
  apiKeys: ApiKeysStatusResponse | null;
  catalog?: ProviderCatalogEntry[];
  openAiSub?: OpenAISubscriptionStatus | null;
  keyModelsMap?: Record<string, string[]>;
  activeRunId?: string | null;
  runAgents?: AgentRecord[];
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
  onAddRunAgent?: (
    runId: string,
    payload: {
      role: AgentRole;
      domain?: string | null;
      parent_agent_id?: string | null;
      model_profile: string;
    }
  ) => Promise<void>;
  onRemoveRunAgent?: (runId: string, agentId: string) => Promise<void>;
  onOpenApiKeys: () => void;
}

const LAYER_META: Array<{
  layer: AgentLayer;
  title: string;
  subtitle: string;
  roles: Array<{ value: AgentRole; label: string }>;
  defaultRole: AgentRole;
  defaultProfile: string;
}> = [
  {
    layer: 1,
    title: "Layer 1 · Orchestration",
    subtitle: "Top-level planners & wave supervisors",
    roles: [{ value: "orchestrator", label: "Orchestrator" }],
    defaultRole: "orchestrator",
    defaultProfile: "@planner",
  },
  {
    layer: 2,
    title: "Layer 2 · Domain Leads",
    subtitle: "Domain architects (Backend, Frontend, Schema, Security, Infra)",
    roles: [{ value: "lead", label: "Domain Lead" }],
    defaultRole: "lead",
    defaultProfile: "@lead",
  },
  {
    layer: 3,
    title: "Layer 3 · Execution & Verification",
    subtitle: "Implementers, black-box G3 test writers, and cross-family G4 reviewers",
    roles: [
      { value: "implementer", label: "Implementer" },
      { value: "test_writer", label: "G3 Test Writer" },
      { value: "reviewer", label: "G4 Reviewer" },
      { value: "integration", label: "Integration" },
      { value: "release", label: "Release" },
    ],
    defaultRole: "implementer",
    defaultProfile: "@coder",
  },
];

const DOMAIN_SUGGESTIONS = ["backend", "frontend", "schema", "security", "integration"];

function resolveProviderCatalog(
  catalog: ProviderCatalogEntry[],
  providerId: string | undefined
): ProviderCatalogEntry | undefined {
  if (!providerId) return undefined;
  const norm = providerId.trim().toLowerCase();
  return catalog.find((c) => c.id === norm || c.default_provider_id === norm);
}

export function AgentLayersStudio({
  compact = false,
  roster,
  models,
  apiKeys,
  catalog = [],
  openAiSub,
  keyModelsMap = {},
  activeRunId,
  runAgents = [],
  onCreateAgentSlot,
  onUpdateAgentSlot,
  onDeleteAgentSlot,
  onAddRunAgent,
  onRemoveRunAgent,
  onOpenApiKeys,
}: AgentLayersStudioProps) {
  const [addingLayer, setAddingLayer] = useState<AgentLayer | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftRole, setDraftRole] = useState<AgentRole>("implementer");
  const [draftDomain, setDraftDomain] = useState("");
  const [draftParentId, setDraftParentId] = useState("");
  const [draftProfile, setDraftProfile] = useState("@coder");
  // Format: "" (profile default) | "key:<NAME>" | "openai_subscription"
  const [draftAuthChoice, setDraftAuthChoice] = useState("");
  const [draftModelName, setDraftModelName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const dialogRef = useRef<HTMLDialogElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (addingLayer !== null) {
      if (!dialog.open) dialog.showModal();
    } else if (dialog.open) {
      dialog.close();
    }
  }, [addingLayer]);

  // Mandatory Safari light-dismiss fallback for browsers lacking <dialog closedby>
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if ("closedBy" in HTMLDialogElement.prototype) return;

    const handleBackdropClick = (event: MouseEvent) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      const inside =
        rect.top <= event.clientY &&
        event.clientY <= rect.top + rect.height &&
        rect.left <= event.clientX &&
        event.clientX <= rect.left + rect.width;
      if (!inside) dialog.close();
    };
    dialog.addEventListener("click", handleBackdropClick);
    return () => dialog.removeEventListener("click", handleBackdropClick);
  }, []);

  const agents = roster?.agents ?? [];
  const profileEntries = models ? Object.entries(models.profiles) : [];
  const savedKeys = apiKeys?.keys ?? [];
  const diversity = models ? checkVerifierDiversity(models) : null;
  const subModels = openAiSub?.models ?? ["gpt-5", "gpt-5-codex", "o4-mini", "gpt-4.1"];

  const modelsForKeyVar = (keyVar: string | null | undefined): string[] => {
    if (!keyVar) return [];
    const custom = keyModelsMap[keyVar];
    if (custom && custom.length > 0) return custom;
    const entry = savedKeys.find((k) => k.name === keyVar);
    const cat = resolveProviderCatalog(catalog, entry?.provider);
    return cat?.models ?? [];
  };

  const openAddDialog = (layer: AgentLayer) => {
    const meta = LAYER_META.find((m) => m.layer === layer)!;
    const parentCandidates = agents.filter((a) => a.layer === layer - 1);
    setAddingLayer(layer);
    setDraftName("");
    setDraftRole(meta.defaultRole);
    setDraftDomain(layer === 1 ? "" : "backend");
    setDraftParentId(parentCandidates[0]?.id ?? "");
    setDraftProfile(meta.defaultProfile);
    if (savedKeys.length > 0) {
      const firstKey = savedKeys[0];
      const avail = modelsForKeyVar(firstKey.name);
      setDraftAuthChoice(`key:${firstKey.name}`);
      setDraftModelName(avail[0] ?? "");
    } else {
      setDraftAuthChoice("");
      setDraftModelName("");
    }
  };

  const handleDraftAuthChange = (choice: string) => {
    setDraftAuthChoice(choice);
    if (choice.startsWith("key:")) {
      const avail = modelsForKeyVar(choice.slice(4));
      setDraftModelName(avail[0] ?? "");
    } else if (choice === "openai_subscription") {
      setDraftModelName(subModels[0] ?? "gpt-5");
    } else {
      setDraftModelName("");
    }
  };

  const handleSaveNewAgent = async () => {
    if (!addingLayer || !draftName.trim() || submitting) return;
    setSubmitting(true);
    try {
      const isKey = draftAuthChoice.startsWith("key:");
      const isSub = draftAuthChoice === "openai_subscription";
      await onCreateAgentSlot({
        name: draftName.trim(),
        layer: addingLayer,
        role: draftRole,
        domain: addingLayer === 1 ? null : draftDomain.trim() || null,
        parent_id: addingLayer === 1 ? null : draftParentId || null,
        model_profile: draftProfile,
        auth_source: isSub ? "openai_subscription" : isKey ? "api_key" : "profile_default",
        api_key_var: isKey ? draftAuthChoice.slice(4) : null,
        model_name: isKey || isSub ? draftModelName.trim() || null : null,
        enabled: true,
      });
      setAddingLayer(null);
    } finally {
      setSubmitting(false);
    }
  };

  const handleSlotAuthChange = async (slot: AgentSlotConfig, choice: string) => {
    if (choice === "") {
      await onUpdateAgentSlot(slot.id, {
        auth_source: "profile_default",
        clear_api_key: true,
        clear_model_name: true,
      });
      return;
    }
    if (choice === "openai_subscription") {
      const picked =
        slot.model_name && subModels.includes(slot.model_name)
          ? slot.model_name
          : (subModels[0] ?? "gpt-5");
      await onUpdateAgentSlot(slot.id, {
        auth_source: "openai_subscription",
        clear_api_key: true,
        model_name: picked,
      });
      return;
    }
    if (choice.startsWith("key:")) {
      const keyVar = choice.slice(4);
      const avail = modelsForKeyVar(keyVar);
      const picked =
        slot.model_name && avail.includes(slot.model_name)
          ? slot.model_name
          : (avail[0] ?? "");
      await onUpdateAgentSlot(slot.id, {
        auth_source: "api_key",
        api_key_var: keyVar,
        ...(picked ? { model_name: picked } : { clear_model_name: true }),
      });
    }
  };

  const parentName = (parentId: string | null): string | null => {
    if (!parentId) return null;
    return agents.find((a) => a.id === parentId)?.name ?? null;
  };

  const activeLayerMeta =
    addingLayer !== null ? LAYER_META.find((m) => m.layer === addingLayer) ?? null : null;
  const parentCandidates =
    addingLayer !== null && addingLayer > 1
      ? agents.filter((a) => a.layer === addingLayer - 1)
      : [];

  const draftKeyEntry = draftAuthChoice.startsWith("key:")
    ? savedKeys.find((k) => k.name === draftAuthChoice.slice(4))
    : undefined;
  const draftProviderCatalog = draftKeyEntry
    ? resolveProviderCatalog(catalog, draftKeyEntry.provider)
    : undefined;
  const draftAvailModels =
    draftAuthChoice === "openai_subscription"
      ? subModels
      : draftAuthChoice.startsWith("key:")
        ? modelsForKeyVar(draftAuthChoice.slice(4))
        : [];

  return (
    <div
      className={
        compact
          ? "h-full min-h-0 overflow-y-auto p-4 space-y-4"
          : "flex-1 min-h-0 overflow-y-auto px-6 sm:px-10 py-8"
      }
    >
      <div className={compact ? "space-y-4 pb-6" : "max-w-[920px] mx-auto space-y-6 pb-10"}>
        {/* Studio Header */}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            {!compact && (
              <div className="flex items-center gap-2.5">
                <Layers className="w-5 h-5" style={{ color: "var(--accent)" }} />
                <h1 className="font-serif text-[30px] font-medium tracking-tight text-[var(--ink-primary)]">
                  3-Layer Agent Studio
                </h1>
              </div>
            )}
            <p className={compact ? "text-[12px] text-[var(--ink-secondary)]" : "text-[13.5px] text-[var(--ink-secondary)] mt-1"}>
              1. Pick each agent&apos;s API key (or OpenAI Subscription). 2. Pick a model filtered
              strictly to that provider.
            </p>
          </div>

          <button
            type="button"
            onClick={onOpenApiKeys}
            className="h-8 px-3 rounded-full border inline-flex items-center gap-1.5 text-[12px] font-medium hover:bg-[var(--bg-hover)] transition-colors shrink-0"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--border-hairline)",
              color: "var(--ink-primary)",
            }}
          >
            <KeyRound className="w-3.5 h-3.5" style={{ color: "var(--accent)" }} />
            API Keys ({savedKeys.length})
          </button>
        </div>

        {/* Cross-Family Verifier Diversity Banner */}
        {diversity &&
          (diversity.diverse ? (
            <div
              className="rounded-xl border px-3 py-2 flex items-center gap-2 text-[12px]"
              style={{
                backgroundColor: "var(--status-nominal-bg)",
                borderColor: "var(--status-nominal-border)",
                color: "var(--ink-primary)",
              }}
            >
              <CheckCircle2
                className="w-3.5 h-3.5 shrink-0"
                style={{ color: "var(--status-nominal)" }}
              />
              <span className="truncate">
                Cross-family G3/G4 ({diversity.implementerFamily} vs{" "}
                {Array.from(new Set(diversity.verifierProfiles.map((v) => v.family))).join(", ")})
              </span>
            </div>
          ) : (
            <div
              className="rounded-xl border px-3 py-2 flex items-center gap-2 text-[12px]"
              style={{
                backgroundColor: "var(--status-danger-bg)",
                borderColor: "var(--status-danger-border)",
                color: "var(--ink-primary)",
              }}
            >
              <AlertTriangle
                className="w-3.5 h-3.5 shrink-0"
                style={{ color: "var(--status-danger)" }}
              />
              <span>
                Verifier diversity warning: {diversity.sameFamilyRoles.join(", ")} share{" "}
                <span className="font-mono">{diversity.implementerFamily}</span>.
              </span>
            </div>
          ))}

        {/* Three Interactive Layers */}
        <div className={compact ? "space-y-4" : "space-y-5"}>
          {LAYER_META.map((layerInfo) => {
            const layerAgents = agents.filter((a) => a.layer === layerInfo.layer);
            return (
              <section
                key={layerInfo.layer}
                className={
                  compact
                    ? "rounded-xl border p-3.5 space-y-3"
                    : "rounded-2xl border p-5 space-y-4"
                }
                style={{
                  backgroundColor: "var(--bg-surface)",
                  borderColor: "var(--border-hairline)",
                }}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2">
                      <span
                        className="px-2 py-0.5 rounded-full font-mono text-[10.5px] font-medium"
                        style={{
                          backgroundColor: "var(--accent-soft)",
                          color: "var(--accent)",
                        }}
                      >
                        L{layerInfo.layer}
                      </span>
                      <h2
                        className={
                          compact
                            ? "font-serif text-[17px] font-medium text-[var(--ink-primary)]"
                            : "font-serif text-[20px] font-medium text-[var(--ink-primary)]"
                        }
                      >
                        {layerInfo.title}
                      </h2>
                      <span className="font-mono text-[11px] text-[var(--ink-muted)] tabular">
                        ({layerAgents.length})
                      </span>
                    </div>
                    {!compact && (
                      <p className="text-[12.5px] text-[var(--ink-muted)] mt-0.5">
                        {layerInfo.subtitle}
                      </p>
                    )}
                  </div>

                  <button
                    type="button"
                    onClick={() => openAddDialog(layerInfo.layer)}
                    className="h-7 px-2.5 rounded-full border inline-flex items-center gap-1 text-[11.5px] font-medium hover:bg-[var(--bg-hover)] transition-colors"
                    style={{
                      backgroundColor: "var(--bg-canvas)",
                      borderColor: "var(--border-hairline)",
                      color: "var(--accent)",
                    }}
                  >
                    <Plus className="w-3 h-3" />
                    Add
                  </button>
                </div>

                {layerAgents.length === 0 ? (
                  <div
                    className="rounded-xl border border-dashed p-3.5 text-center text-[12px] text-[var(--ink-muted)]"
                    style={{ borderColor: "var(--border-strong)" }}
                  >
                    No agents in Layer {layerInfo.layer}.{" "}
                    <button
                      type="button"
                      onClick={() => openAddDialog(layerInfo.layer)}
                      className="underline font-medium"
                      style={{ color: "var(--accent)" }}
                    >
                      Add agent
                    </button>
                  </div>
                ) : (
                  <div className={compact ? "space-y-2.5" : "grid grid-cols-1 md:grid-cols-2 gap-3"}>
                    {layerAgents.map((slot: AgentSlotConfig) => {
                      const normProf = normalizeProfileKey(slot.model_profile);
                      const family = models ? resolveProfileFamily(models, normProf) : "unknown";
                      const pName = parentName(slot.parent_id);

                      const authValue =
                        slot.auth_source === "openai_subscription"
                          ? "openai_subscription"
                          : slot.auth_source === "api_key" && slot.api_key_var
                            ? `key:${slot.api_key_var}`
                            : "";

                      const keyEntry =
                        slot.auth_source === "api_key" && slot.api_key_var
                          ? savedKeys.find((k) => k.name === slot.api_key_var)
                          : undefined;
                      const keyCatalog = resolveProviderCatalog(catalog, keyEntry?.provider);
                      const keyAvailModels =
                        slot.auth_source === "openai_subscription"
                          ? subModels
                          : slot.auth_source === "api_key"
                            ? modelsForKeyVar(slot.api_key_var)
                            : [];
                      const selectedDirectModel =
                        slot.model_name || keyAvailModels[0] || "";

                      return (
                        <div
                          key={slot.id}
                          className="rounded-xl border p-3 space-y-2.5"
                          style={{
                            backgroundColor: "var(--bg-canvas)",
                            borderColor: "var(--border-hairline)",
                            opacity: slot.enabled ? 1 : 0.62,
                          }}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5">
                                <span className="text-[13px] font-semibold text-[var(--ink-primary)] truncate">
                                  {slot.name}
                                </span>
                                {slot.domain && (
                                  <span
                                    className="px-1.5 py-0.5 rounded-full font-mono text-[10px]"
                                    style={{
                                      backgroundColor: "var(--bg-active)",
                                      color: "var(--ink-secondary)",
                                    }}
                                  >
                                    {slot.domain}
                                  </span>
                                )}
                              </div>
                              <div className="text-[11px] text-[var(--ink-muted)] mt-0.5 flex flex-wrap items-center gap-1">
                                <span>{slot.role.replace(/_/g, " ")}</span>
                                {pName && <span>· ↑ {pName}</span>}
                                {slot.auth_source === "api_key" && keyEntry ? (
                                  <span style={{ color: "var(--accent)" }}>
                                    · {keyEntry.provider_label}
                                  </span>
                                ) : slot.auth_source === "openai_subscription" ? (
                                  <span style={{ color: "var(--accent)" }}>
                                    · Subscription CLI (single-shot)
                                  </span>
                                ) : family !== "unknown" ? (
                                  <span>· {family}</span>
                                ) : null}
                              </div>
                            </div>

                            <div className="flex items-center gap-1 shrink-0">
                              <input
                                type="checkbox"
                                checked={slot.enabled}
                                title={slot.enabled ? "Disable agent" : "Enable agent"}
                                aria-label={`Enable ${slot.name}`}
                                onChange={(e) =>
                                  void onUpdateAgentSlot(slot.id, { enabled: e.target.checked })
                                }
                                className="w-3.5 h-3.5"
                              />
                              <button
                                type="button"
                                onClick={() => void onDeleteAgentSlot(slot.id)}
                                title={`Remove ${slot.name}`}
                                aria-label={`Remove ${slot.name}`}
                                className="h-7 w-7 rounded-lg flex items-center justify-center text-[var(--ink-muted)] hover:text-[var(--status-danger)] hover:bg-[var(--bg-hover)] transition-colors"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </div>

                          {/* 2-Step: 1) API Key / Auth Source -> 2) Provider-Filtered Model */}
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                            <label className="space-y-1">
                              <span className="block text-[10.5px] text-[var(--ink-muted)]">
                                1. API Key / Auth Source
                              </span>
                              <select
                                aria-label={`API key variable for ${slot.name}`}
                                value={authValue}
                                onChange={(e) =>
                                  void handleSlotAuthChange(slot, e.target.value)
                                }
                                className="w-full h-8 rounded-lg border px-2 font-mono text-[11px] truncate"
                                style={{
                                  backgroundColor: "var(--bg-surface)",
                                  borderColor: "var(--border-hairline)",
                                  color: authValue
                                    ? "var(--accent)"
                                    : "var(--ink-secondary)",
                                }}
                              >
                                <option value="">Profile default ({normProf})</option>
                                {savedKeys.map((k) => (
                                  <option key={k.name} value={`key:${k.name}`}>
                                    {k.name} · {k.provider_label}
                                  </option>
                                ))}
                                {(openAiSub?.enabled ||
                                  slot.auth_source === "openai_subscription") && (
                                  <option value="openai_subscription">
                                    OpenAI Subscription (Codex CLI)
                                  </option>
                                )}
                              </select>
                            </label>

                            <label className="space-y-1">
                              <span className="block text-[10.5px] text-[var(--ink-muted)]">
                                {slot.auth_source === "api_key"
                                  ? `2. Model (${keyEntry?.provider_label ?? "Key"})`
                                  : slot.auth_source === "openai_subscription"
                                    ? "2. Model (Codex CLI)"
                                    : "2. Model Profile"}
                              </span>
                              {slot.auth_source === "profile_default" ? (
                                <select
                                  aria-label={`Model profile for ${slot.name}`}
                                  value={normProf}
                                  onChange={(e) =>
                                    void onUpdateAgentSlot(slot.id, {
                                      model_profile: e.target.value,
                                    })
                                  }
                                  className="w-full h-8 rounded-lg border px-2 font-mono text-[11px] truncate"
                                  style={{
                                    backgroundColor: "var(--bg-surface)",
                                    borderColor: "var(--border-hairline)",
                                    color: "var(--ink-primary)",
                                  }}
                                >
                                  {profileEntries.map(([pk, prof]) => {
                                    const normKey = normalizeProfileKey(pk);
                                    return (
                                      <option key={normKey} value={normKey}>
                                        {normKey} · {prof.model}
                                      </option>
                                    );
                                  })}
                                </select>
                              ) : keyAvailModels.length > 0 ? (
                                <select
                                  aria-label={`Model for ${slot.name}`}
                                  value={selectedDirectModel}
                                  onChange={(e) =>
                                    void onUpdateAgentSlot(slot.id, {
                                      model_name: e.target.value,
                                    })
                                  }
                                  className="w-full h-8 rounded-lg border px-2 font-mono text-[11px] truncate"
                                  style={{
                                    backgroundColor: "var(--bg-surface)",
                                    borderColor: "var(--border-hairline)",
                                    color: "var(--ink-primary)",
                                  }}
                                >
                                  {keyAvailModels.map((m) => (
                                    <option key={m} value={m}>
                                      {m}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <input
                                  type="text"
                                  aria-label={`Custom model for ${slot.name}`}
                                  defaultValue={slot.model_name ?? ""}
                                  placeholder={
                                    keyCatalog?.allow_custom_model
                                      ? "Custom model ID…"
                                      : "Enter model ID…"
                                  }
                                  onBlur={(e) => {
                                    const val = e.target.value.trim();
                                    if (val !== (slot.model_name ?? "")) {
                                      void onUpdateAgentSlot(slot.id, {
                                        model_name: val || null,
                                        ...(val ? {} : { clear_model_name: true }),
                                      });
                                    }
                                  }}
                                  className="w-full h-8 rounded-lg border px-2 font-mono text-[11px]"
                                  style={{
                                    backgroundColor: "var(--bg-surface)",
                                    borderColor: "var(--border-hairline)",
                                    color: "var(--ink-primary)",
                                  }}
                                />
                              )}
                            </label>
                          </div>

                          {activeRunId && onAddRunAgent && slot.enabled && (
                            <div className="pt-0.5 flex justify-end">
                              <button
                                type="button"
                                onClick={() =>
                                  void onAddRunAgent(activeRunId, {
                                    role: slot.role,
                                    domain: slot.domain,
                                    model_profile: slot.model_profile,
                                  })
                                }
                                className="text-[11px] font-medium hover:underline"
                                style={{ color: "var(--accent)" }}
                              >
                                + Spawn onto current run
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>
            );
          })}
        </div>

        {/* Active Run Live Threads */}
        {activeRunId && runAgents.length > 0 && (
          <section
            className="rounded-xl border p-4 space-y-2.5"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--border-hairline)",
            }}
          >
            <h2 className="font-serif text-[17px] font-medium text-[var(--ink-primary)]">
              Active Run Instances ({runAgents.filter((a) => a.status !== "removed").length})
            </h2>
            <div
              className="rounded-xl border divide-y"
              style={{
                backgroundColor: "var(--bg-canvas)",
                borderColor: "var(--border-hairline)",
              }}
            >
              {runAgents
                .filter((a) => a.status !== "removed")
                .map((agent) => (
                  <div
                    key={agent.id}
                    className="px-3 py-2 flex items-center justify-between gap-2 text-[12px]"
                    style={{ borderColor: "var(--border-hairline)" }}
                  >
                    <div className="min-w-0 truncate">
                      <span className="font-medium text-[var(--ink-primary)]">
                        {agent.role}
                        {agent.domain ? ` · ${agent.domain}` : ""}
                      </span>
                      <span className="ml-2 font-mono text-[10.5px] text-[var(--ink-muted)]">
                        {normalizeProfileKey(agent.model_profile)}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className="font-mono text-[10.5px] text-[var(--ink-secondary)]">
                        {agent.status}
                      </span>
                      {onRemoveRunAgent && (
                        <button
                          type="button"
                          onClick={() => void onRemoveRunAgent(activeRunId, agent.id)}
                          aria-label={`Remove ${agent.role} from active run`}
                          className="text-[11px] text-[var(--ink-muted)] hover:text-[var(--status-danger)]"
                        >
                          Remove
                        </button>
                      )}
                    </div>
                  </div>
                ))}
            </div>
          </section>
        )}
      </div>

      {/* Add Agent Dialog (<dialog closedby="any"> + Safari fallback) */}
      <dialog
        ref={dialogRef}
        closedby="any"
        aria-labelledby="add-agent-modal-title"
        className="foundry-dialog"
        onClose={() => setAddingLayer(null)}
      >
        {activeLayerMeta && (
          <div className="flex flex-col max-w-[520px] w-full mx-auto">
            <div
              className="px-6 py-4 border-b flex items-center justify-between gap-3"
              style={{ borderColor: "var(--border-hairline)" }}
            >
              <div>
                <h2
                  id="add-agent-modal-title"
                  className="font-serif text-[20px] font-medium text-[var(--ink-primary)]"
                >
                  Add Layer {activeLayerMeta.layer} Agent
                </h2>
                <p className="text-[12px] text-[var(--ink-muted)]">
                  {activeLayerMeta.title}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setAddingLayer(null)}
                aria-label="Close add agent dialog"
                className="h-8 w-8 rounded-full flex items-center justify-center hover:bg-[var(--bg-hover)] text-[var(--ink-muted)]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-6 space-y-4 text-[13px]">
              <label className="block space-y-1">
                <span className="text-[12px] font-medium text-[var(--ink-primary)]">
                  Agent Name
                </span>
                <input
                  type="text"
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  placeholder={
                    activeLayerMeta.layer === 1
                      ? "e.g. Architecture Orchestrator"
                      : activeLayerMeta.layer === 2
                        ? "e.g. Security Lead"
                        : "e.g. Payments Implementer"
                  }
                  className="w-full h-9 px-3 rounded-xl border"
                  style={{
                    backgroundColor: "var(--bg-canvas)",
                    borderColor: "var(--border-hairline)",
                    color: "var(--ink-primary)",
                  }}
                />
              </label>

              <div className="grid grid-cols-2 gap-3">
                <label className="block space-y-1">
                  <span className="text-[12px] font-medium text-[var(--ink-primary)]">Role</span>
                  <select
                    value={draftRole}
                    onChange={(e) => setDraftRole(e.target.value as AgentRole)}
                    className="w-full h-9 px-2.5 rounded-xl border"
                    style={{
                      backgroundColor: "var(--bg-canvas)",
                      borderColor: "var(--border-hairline)",
                      color: "var(--ink-primary)",
                    }}
                  >
                    {activeLayerMeta.roles.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block space-y-1">
                  <span className="text-[12px] font-medium text-[var(--ink-primary)]">
                    Fallback Profile
                  </span>
                  <select
                    value={draftProfile}
                    onChange={(e) => setDraftProfile(e.target.value)}
                    className="w-full h-9 px-2.5 rounded-xl border font-mono text-[11.5px]"
                    style={{
                      backgroundColor: "var(--bg-canvas)",
                      borderColor: "var(--border-hairline)",
                      color: "var(--ink-primary)",
                    }}
                  >
                    {profileEntries.map(([pk, prof]) => {
                      const normKey = normalizeProfileKey(pk);
                      return (
                        <option key={normKey} value={normKey}>
                          {normKey} · {prof.model}
                        </option>
                      );
                    })}
                  </select>
                </label>
              </div>

              {activeLayerMeta.layer > 1 && (
                <div className="grid grid-cols-2 gap-3">
                  <label className="block space-y-1">
                    <span className="text-[12px] font-medium text-[var(--ink-primary)]">
                      Domain
                    </span>
                    <input
                      type="text"
                      value={draftDomain}
                      onChange={(e) => setDraftDomain(e.target.value)}
                      placeholder="backend, frontend, security…"
                      list="foundry-domain-presets"
                      className="w-full h-9 px-3 rounded-xl border font-mono text-[12px]"
                      style={{
                        backgroundColor: "var(--bg-canvas)",
                        borderColor: "var(--border-hairline)",
                        color: "var(--ink-primary)",
                      }}
                    />
                    <datalist id="foundry-domain-presets">
                      {DOMAIN_SUGGESTIONS.map((d) => (
                        <option key={d} value={d} />
                      ))}
                    </datalist>
                  </label>

                  <label className="block space-y-1">
                    <span className="text-[12px] font-medium text-[var(--ink-primary)]">
                      Reports To (Layer {activeLayerMeta.layer - 1})
                    </span>
                    <select
                      value={draftParentId}
                      onChange={(e) => setDraftParentId(e.target.value)}
                      className="w-full h-9 px-2.5 rounded-xl border text-[12px]"
                      style={{
                        backgroundColor: "var(--bg-canvas)",
                        borderColor: "var(--border-hairline)",
                        color: "var(--ink-primary)",
                      }}
                    >
                      <option value="">None (top-level in layer)</option>
                      {parentCandidates.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                          {p.domain ? ` (${p.domain})` : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label className="block space-y-1">
                  <span className="text-[12px] font-medium text-[var(--ink-primary)]">
                    1. API Key / Auth Source
                  </span>
                  <select
                    value={draftAuthChoice}
                    onChange={(e) => handleDraftAuthChange(e.target.value)}
                    className="w-full h-9 px-2.5 rounded-xl border font-mono text-[11.5px]"
                    style={{
                      backgroundColor: "var(--bg-canvas)",
                      borderColor: "var(--border-hairline)",
                      color: "var(--ink-primary)",
                    }}
                  >
                    <option value="">Use Fallback Profile ({draftProfile})</option>
                    {savedKeys.map((k) => (
                      <option key={k.name} value={`key:${k.name}`}>
                        {k.name} · {k.provider_label}
                      </option>
                    ))}
                    {openAiSub?.enabled && (
                      <option value="openai_subscription">
                        OpenAI Subscription (Codex CLI)
                      </option>
                    )}
                  </select>
                </label>

                <label className="block space-y-1">
                  <span className="text-[12px] font-medium text-[var(--ink-primary)]">
                    2. Model{" "}
                    {draftKeyEntry
                      ? `(${draftKeyEntry.provider_label})`
                      : draftAuthChoice === "openai_subscription"
                        ? "(Codex CLI)"
                        : "(From Profile)"}
                  </span>
                  {draftAuthChoice === "" ? (
                    <div
                      className="h-9 px-3 rounded-xl border flex items-center font-mono text-[11.5px] text-[var(--ink-muted)]"
                      style={{
                        backgroundColor: "var(--bg-canvas)",
                        borderColor: "var(--border-hairline)",
                      }}
                    >
                      {models?.profiles[draftProfile]?.model ?? draftProfile}
                    </div>
                  ) : draftAvailModels.length > 0 ? (
                    <select
                      value={draftModelName}
                      onChange={(e) => setDraftModelName(e.target.value)}
                      className="w-full h-9 px-2.5 rounded-xl border font-mono text-[11.5px]"
                      style={{
                        backgroundColor: "var(--bg-canvas)",
                        borderColor: "var(--border-hairline)",
                        color: "var(--ink-primary)",
                      }}
                    >
                      {draftAvailModels.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      value={draftModelName}
                      onChange={(e) => setDraftModelName(e.target.value)}
                      placeholder={
                        draftProviderCatalog?.allow_custom_model
                          ? "Enter model ID…"
                          : "Enter model ID…"
                      }
                      className="w-full h-9 px-3 rounded-xl border font-mono text-[11.5px]"
                      style={{
                        backgroundColor: "var(--bg-canvas)",
                        borderColor: "var(--border-hairline)",
                        color: "var(--ink-primary)",
                      }}
                    />
                  )}
                </label>
              </div>

              <div className="pt-2 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setAddingLayer(null)}
                  className="h-9 px-4 rounded-full border text-[12.5px] font-medium hover:bg-[var(--bg-hover)]"
                  style={{
                    borderColor: "var(--border-hairline)",
                    color: "var(--ink-secondary)",
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={!draftName.trim() || submitting}
                  onClick={() => void handleSaveNewAgent()}
                  className="h-9 px-4 rounded-full text-[12.5px] font-medium disabled:opacity-45"
                  style={{
                    backgroundColor: "var(--accent)",
                    color: "#ffffff",
                  }}
                >
                  {submitting ? "Adding…" : "Add to Layer " + activeLayerMeta.layer}
                </button>
              </div>
            </div>
          </div>
        )}
      </dialog>
    </div>
  );
}
