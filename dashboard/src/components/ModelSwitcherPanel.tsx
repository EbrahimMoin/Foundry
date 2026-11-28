import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Edit3,
  KeyRound,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import type {
  ApiKeysStatusResponse,
  DataPolicyConfig,
  KeyModelsResponse,
  ModelProfileUpsertPayload,
  ModelsConfig,
  OpenAISubscriptionStatus,
  ProviderCatalogEntry,
} from "../types";
import {
  checkVerifierDiversity,
  normalizeProfileKey,
  resolveProfileFamily,
} from "../utils/foundryMetrics";

interface ModelSwitcherPanelProps {
  models: ModelsConfig | null;
  dataPolicy: DataPolicyConfig | null;
  apiKeys?: ApiKeysStatusResponse | null;
  catalog?: ProviderCatalogEntry[];
  openAiSub?: OpenAISubscriptionStatus | null;
  keyModelsMap?: Record<string, string[]>;
  onUpdateRole: (role: string, profile: string) => Promise<void>;
  onUpsertProfile?: (payload: ModelProfileUpsertPayload) => Promise<void>;
  onDeleteProfile?: (profileName: string) => Promise<void>;
  onFetchKeyModels?: (keyName: string) => Promise<KeyModelsResponse>;
  onUpdateDataPolicy: (patch: Partial<DataPolicyConfig>) => Promise<void>;
  onOpenApiKeys?: () => void;
}

const ROLE_ORDER = [
  "orchestrator",
  "lead",
  "implementer",
  "test_writer",
  "reviewer",
  "integration",
  "release",
];

function catalogForProviderId(
  catalog: ProviderCatalogEntry[],
  providerId: string
): ProviderCatalogEntry | undefined {
  const norm = providerId.trim().toLowerCase();
  return (
    catalog.find((c) => c.id === norm || c.default_provider_id === norm) ??
    catalog.find((c) => norm.startsWith("key_") && c.family === norm)
  );
}

export function ModelSwitcherPanel({
  models,
  dataPolicy,
  apiKeys,
  catalog = [],
  openAiSub,
  keyModelsMap = {},
  onUpdateRole,
  onUpsertProfile,
  onDeleteProfile,
  onFetchKeyModels,
  onUpdateDataPolicy,
  onOpenApiKeys,
}: ModelSwitcherPanelProps) {
  const [editorOpen, setEditorOpen] = useState(false);
  const [profileName, setProfileName] = useState("@custom");
  // Format of authChoice:
  // "key:<KEY_VAR>" | "openai_subscription" | "provider:<catalog_id_or_provider_id>"
  const [authChoice, setAuthChoice] = useState<string>("");
  const [modelName, setModelName] = useState<string>("");
  const [customModel, setCustomModel] = useState<string>("");
  const [priceIn, setPriceIn] = useState<string>("0");
  const [priceOut, setPriceOut] = useState<string>("0");
  const [saving, setSaving] = useState(false);
  const [refreshingModels, setRefreshingModels] = useState(false);

  const savedKeys = apiKeys?.keys ?? [];

  const resolvedContext = useMemo(() => {
    if (authChoice.startsWith("key:")) {
      const keyVar = authChoice.slice(4);
      const keyEntry = savedKeys.find((k) => k.name === keyVar);
      const cat = keyEntry ? catalogForProviderId(catalog, keyEntry.provider) : undefined;
      const liveModels = keyModelsMap[keyVar];
      const availableModels =
        liveModels && liveModels.length > 0 ? liveModels : (cat?.models ?? []);
      return {
        authSource: "api_key" as const,
        apiKeyVar: keyVar,
        providerId: keyEntry?.provider ?? "custom",
        providerLabel: keyEntry?.provider_label ?? cat?.label ?? "API Key",
        availableModels,
        allowCustom: cat?.allow_custom_model ?? availableModels.length === 0,
      };
    }
    if (authChoice === "openai_subscription") {
      const cat = catalogForProviderId(catalog, "openai_codex_cli");
      return {
        authSource: "openai_subscription" as const,
        apiKeyVar: null,
        providerId: "openai_codex_cli",
        providerLabel: "OpenAI Subscription (Codex CLI)",
        availableModels: openAiSub?.models ?? cat?.models ?? ["gpt-5", "gpt-5-codex", "o4-mini"],
        allowCustom: false,
      };
    }
    const rawProv = authChoice.startsWith("provider:") ? authChoice.slice(9) : "gemini";
    const cat = catalogForProviderId(catalog, rawProv);
    return {
      authSource: "provider_default" as const,
      apiKeyVar: null,
      providerId: cat?.id ?? rawProv,
      providerLabel: cat?.label ?? rawProv,
      availableModels: cat?.models ?? [],
      allowCustom: cat?.allow_custom_model ?? true,
    };
  }, [authChoice, savedKeys, catalog, keyModelsMap, openAiSub]);

  if (!models) {
    return (
      <div className="p-4 text-[12.5px] text-[var(--ink-muted)]">Loading models…</div>
    );
  }

  const profileKeys = Object.keys(models.profiles);
  const roles = Object.keys(models.assignments.roles).sort((a, b) => {
    const ia = ROLE_ORDER.indexOf(a);
    const ib = ROLE_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });
  const diversity = checkVerifierDiversity(models);
  const providers = Object.entries(models.providers);
  const assignedProfileSet = new Set(Object.values(models.assignments.roles).map(normalizeProfileKey));

  const openNewProfile = () => {
    const firstKey = savedKeys[0];
    const defaultChoice = firstKey ? `key:${firstKey.name}` : "provider:gemini";
    const cat = firstKey
      ? catalogForProviderId(catalog, firstKey.provider)
      : catalogForProviderId(catalog, "gemini");
    const firstModel =
      (firstKey ? keyModelsMap[firstKey.name]?.[0] : undefined) ?? cat?.models[0] ?? "gemini-2.5-flash";
    setProfileName("@custom_model");
    setAuthChoice(defaultChoice);
    setModelName(firstModel);
    setCustomModel("");
    setPriceIn("0");
    setPriceOut("0");
    setEditorOpen(true);
  };

  const openEditProfile = (pk: string) => {
    const norm = normalizeProfileKey(pk);
    const prof = models.profiles[pk];
    const provCfg = models.providers[prof.provider];
    let choice = `provider:${prof.provider}`;
    if (provCfg?.transport === "openai_codex_cli" || prof.provider === "openai_subscription_cli") {
      choice = "openai_subscription";
    } else if (provCfg?.key_ref) {
      const matchedKey = savedKeys.find((k) => k.key_ref === provCfg.key_ref);
      if (matchedKey) {
        choice = `key:${matchedKey.name}`;
      }
    } else {
      const cat = catalogForProviderId(catalog, prof.provider);
      if (cat) choice = `provider:${cat.id}`;
    }
    setProfileName(norm);
    setAuthChoice(choice);
    setModelName(prof.model);
    setCustomModel(prof.model);
    setPriceIn(String(prof.price?.in ?? 0));
    setPriceOut(String(prof.price?.out ?? 0));
    setEditorOpen(true);
  };

  const handleAuthChoiceChange = (nextChoice: string) => {
    setAuthChoice(nextChoice);
    let nextModels: string[] = [];
    if (nextChoice.startsWith("key:")) {
      const kv = nextChoice.slice(4);
      const kEntry = savedKeys.find((k) => k.name === kv);
      const cat = kEntry ? catalogForProviderId(catalog, kEntry.provider) : undefined;
      nextModels = keyModelsMap[kv]?.length ? keyModelsMap[kv] : (cat?.models ?? []);
    } else if (nextChoice === "openai_subscription") {
      nextModels = openAiSub?.models ?? ["gpt-5", "gpt-5-codex", "o4-mini"];
    } else if (nextChoice.startsWith("provider:")) {
      const cat = catalogForProviderId(catalog, nextChoice.slice(9));
      nextModels = cat?.models ?? [];
    }
    if (nextModels.length > 0 && !nextModels.includes(modelName)) {
      setModelName(nextModels[0]);
      setCustomModel(nextModels[0]);
    } else if (nextModels.length === 0) {
      setModelName("");
      setCustomModel("");
    }
  };

  const handleSaveProfile = async () => {
    if (!onUpsertProfile) return;
    const effectiveModel =
      resolvedContext.allowCustom && (!resolvedContext.availableModels.includes(modelName) || !modelName)
        ? customModel.trim() || modelName.trim()
        : modelName.trim();
    if (!profileName.trim() || !effectiveModel || saving) return;

    setSaving(true);
    try {
      await onUpsertProfile({
        profile_name: profileName.trim(),
        auth_source: resolvedContext.authSource,
        api_key_var: resolvedContext.apiKeyVar,
        provider: resolvedContext.providerId,
        model: effectiveModel,
        input_per_m: Number(priceIn) || 0,
        output_per_m: Number(priceOut) || 0,
      });
      setEditorOpen(false);
    } finally {
      setSaving(false);
    }
  };

  const handleRefreshKeyModels = async () => {
    if (!onFetchKeyModels || !resolvedContext.apiKeyVar) return;
    setRefreshingModels(true);
    try {
      const res = await onFetchKeyModels(resolvedContext.apiKeyVar);
      if (res.models.length > 0 && !res.models.includes(modelName)) {
        setModelName(res.models[0]);
      }
    } finally {
      setRefreshingModels(false);
    }
  };

  const describeProfileSource = (providerId: string): string => {
    const provCfg = models.providers[providerId];
    if (provCfg?.transport === "openai_codex_cli") {
      return "OpenAI Subscription (Codex CLI)";
    }
    if (provCfg?.key_ref) {
      const matchedKey = savedKeys.find((k) => k.key_ref === provCfg.key_ref);
      if (matchedKey) return `${matchedKey.name} · ${matchedKey.provider_label}`;
    }
    const cat = catalogForProviderId(catalog, providerId);
    return cat?.label ?? providerId;
  };

  return (
    <div className="p-4 space-y-5 overflow-y-auto h-full">
      {diversity.diverse ? (
        <div className="flex items-center justify-between gap-2 text-[12px] text-[var(--ink-secondary)]">
          <div className="flex items-center gap-2 min-w-0">
            <CheckCircle2
              className="w-4 h-4 shrink-0"
              style={{ color: "var(--status-nominal)" }}
            />
            <span className="truncate">
              Cross-family ({diversity.implementerFamily} ×{" "}
              {Array.from(new Set(diversity.verifierProfiles.map((v) => v.family))).join(", ")})
            </span>
          </div>
          {onOpenApiKeys && (
            <button
              type="button"
              onClick={onOpenApiKeys}
              className="inline-flex items-center gap-1 text-[11.5px] font-medium shrink-0 hover:underline"
              style={{ color: "var(--accent)" }}
            >
              <KeyRound className="w-3 h-3" />
              API Keys ({savedKeys.length})
            </button>
          )}
        </div>
      ) : (
        <div
          className="rounded-xl border p-3 flex items-start gap-2.5 text-[12px]"
          style={{
            backgroundColor: "var(--status-danger-bg)",
            borderColor: "var(--status-danger-border)",
          }}
          role="status"
        >
          <AlertTriangle
            className="w-4 h-4 shrink-0 mt-0.5"
            style={{ color: "var(--status-danger)" }}
          />
          <div>
            <div className="font-semibold" style={{ color: "var(--status-danger)" }}>
              Same-family verifier collision
            </div>
            <p className="text-[var(--ink-secondary)] mt-0.5 leading-snug">
              {diversity.sameFamilyRoles.join(", ")} share{" "}
              <span className="font-mono">{diversity.implementerFamily}</span> with implementer.
            </p>
          </div>
        </div>
      )}

      {/* Editable Model Profiles (Key-First -> Filtered Models) */}
      <section aria-label="Model profiles" className="space-y-2.5">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-[12px] font-semibold text-[var(--ink-primary)]">
            Model Profiles (`models.yaml`)
          </h2>
          {onUpsertProfile && (
            <button
              type="button"
              onClick={openNewProfile}
              className="h-7 px-2.5 rounded-full border inline-flex items-center gap-1 text-[11.5px] font-medium hover:bg-[var(--bg-hover)]"
              style={{
                backgroundColor: "var(--bg-surface)",
                borderColor: "var(--border-hairline)",
                color: "var(--accent)",
              }}
            >
              <Plus className="w-3 h-3" />
              Add Profile
            </button>
          )}
        </div>

        {editorOpen && onUpsertProfile && (
          <div
            className="rounded-xl border p-3.5 space-y-3"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--accent)",
            }}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px] font-semibold text-[var(--ink-primary)]">
                Configure Model Profile
              </span>
              <button
                type="button"
                onClick={() => setEditorOpen(false)}
                className="text-[11px] text-[var(--ink-muted)] hover:text-[var(--ink-primary)]"
              >
                Cancel
              </button>
            </div>

            <label className="block space-y-1">
              <span className="text-[11px] text-[var(--ink-muted)]">Profile Name</span>
              <input
                type="text"
                value={profileName}
                onChange={(e) => setProfileName(e.target.value)}
                placeholder="@planner"
                className="w-full h-8 px-2.5 rounded-lg border font-mono text-[12px]"
                style={{
                  backgroundColor: "var(--bg-canvas)",
                  borderColor: "var(--border-hairline)",
                  color: "var(--ink-primary)",
                }}
              />
            </label>

            <label className="block space-y-1">
              <span className="text-[11px] text-[var(--ink-muted)]">
                1. API Key / Auth Source
              </span>
              <select
                aria-label="Profile auth source"
                value={authChoice}
                onChange={(e) => handleAuthChoiceChange(e.target.value)}
                className="w-full h-8 px-2 rounded-lg border text-[12px]"
                style={{
                  backgroundColor: "var(--bg-canvas)",
                  borderColor: "var(--border-hairline)",
                  color: "var(--ink-primary)",
                }}
              >
                {savedKeys.length > 0 && (
                  <optgroup label="Saved API Keys">
                    {savedKeys.map((k) => (
                      <option key={k.name} value={`key:${k.name}`}>
                        {k.name} · {k.provider_label} ({k.masked_preview})
                      </option>
                    ))}
                  </optgroup>
                )}
                {openAiSub?.enabled && (
                  <optgroup label="Subscription CLI (single-shot)">
                    <option value="openai_subscription">
                      OpenAI Subscription (Codex CLI)
                    </option>
                  </optgroup>
                )}
                <optgroup label="Built-in SDK Providers">
                  {catalog
                    .filter((c) => c.id !== "openai_codex_cli")
                    .map((c) => (
                      <option key={c.id} value={`provider:${c.id}`}>
                        {c.label}
                      </option>
                    ))}
                </optgroup>
              </select>
            </label>

            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-[var(--ink-muted)]">
                  2. Model ({resolvedContext.providerLabel} only)
                </span>
                {resolvedContext.apiKeyVar && onFetchKeyModels && (
                  <button
                    type="button"
                    disabled={refreshingModels}
                    onClick={() => void handleRefreshKeyModels()}
                    className="inline-flex items-center gap-1 text-[10.5px] font-medium hover:underline"
                    style={{ color: "var(--accent)" }}
                  >
                    <RefreshCw
                      className={`w-2.5 h-2.5 ${refreshingModels ? "animate-spin" : ""}`}
                    />
                    Refresh models
                  </button>
                )}
              </div>

              {resolvedContext.availableModels.length > 0 ? (
                <select
                  aria-label="Profile model"
                  value={modelName}
                  onChange={(e) => {
                    setModelName(e.target.value);
                    setCustomModel(e.target.value);
                  }}
                  className="w-full h-8 px-2 rounded-lg border font-mono text-[11.5px]"
                  style={{
                    backgroundColor: "var(--bg-canvas)",
                    borderColor: "var(--border-hairline)",
                    color: "var(--ink-primary)",
                  }}
                >
                  {resolvedContext.availableModels.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                  {resolvedContext.allowCustom &&
                    customModel &&
                    !resolvedContext.availableModels.includes(customModel) && (
                      <option value={customModel}>{customModel}</option>
                    )}
                </select>
              ) : null}

              {resolvedContext.allowCustom && (
                <input
                  type="text"
                  value={customModel}
                  onChange={(e) => {
                    setCustomModel(e.target.value);
                    setModelName(e.target.value);
                  }}
                  placeholder="Enter custom model ID…"
                  aria-label="Custom model ID"
                  className="w-full h-8 px-2.5 rounded-lg border font-mono text-[11.5px]"
                  style={{
                    backgroundColor: "var(--bg-canvas)",
                    borderColor: "var(--border-hairline)",
                    color: "var(--ink-primary)",
                  }}
                />
              )}
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="space-y-1">
                <span className="text-[10.5px] text-[var(--ink-muted)]">Input $/1M</span>
                <input
                  type="number"
                  min={0}
                  step="0.05"
                  value={priceIn}
                  onChange={(e) => setPriceIn(e.target.value)}
                  className="w-full h-7 px-2 rounded-lg border font-mono text-[11px]"
                  style={{
                    backgroundColor: "var(--bg-canvas)",
                    borderColor: "var(--border-hairline)",
                    color: "var(--ink-primary)",
                  }}
                />
              </label>
              <label className="space-y-1">
                <span className="text-[10.5px] text-[var(--ink-muted)]">Output $/1M</span>
                <input
                  type="number"
                  min={0}
                  step="0.05"
                  value={priceOut}
                  onChange={(e) => setPriceOut(e.target.value)}
                  className="w-full h-7 px-2 rounded-lg border font-mono text-[11px]"
                  style={{
                    backgroundColor: "var(--bg-canvas)",
                    borderColor: "var(--border-hairline)",
                    color: "var(--ink-primary)",
                  }}
                />
              </label>
            </div>

            <div className="flex justify-end gap-2">
              <button
                type="button"
                disabled={!profileName.trim() || (!modelName.trim() && !customModel.trim()) || saving}
                onClick={() => void handleSaveProfile()}
                className="h-8 px-3.5 rounded-lg text-[12px] font-medium disabled:opacity-45"
                style={{ backgroundColor: "var(--accent)", color: "#ffffff" }}
              >
                {saving ? "Saving…" : "Save Profile"}
              </button>
            </div>
          </div>
        )}

        <div
          className="rounded-xl border divide-y"
          style={{
            backgroundColor: "var(--bg-surface)",
            borderColor: "var(--border-hairline)",
          }}
        >
          {profileKeys.map((pk) => {
            const normKey = normalizeProfileKey(pk);
            const prof = models.profiles[pk];
            const inUse = assignedProfileSet.has(normKey);
            return (
              <div
                key={normKey}
                className="px-3 py-2 flex items-center justify-between gap-2"
                style={{ borderColor: "var(--border-hairline)" }}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-[12px] font-semibold text-[var(--ink-primary)]">
                      {normKey}
                    </span>
                    <span className="font-mono text-[11px] text-[var(--accent)] truncate">
                      {prof.model}
                    </span>
                  </div>
                  <div className="text-[10.5px] text-[var(--ink-muted)] truncate">
                    {describeProfileSource(prof.provider)}
                    {prof.price && (prof.price.in > 0 || prof.price.out > 0)
                      ? ` · $${prof.price.in}/$${prof.price.out}`
                      : ""}
                  </div>
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  {onUpsertProfile && (
                    <button
                      type="button"
                      onClick={() => openEditProfile(pk)}
                      aria-label={`Edit profile ${normKey}`}
                      title={`Edit ${normKey}`}
                      className="h-7 w-7 rounded-lg flex items-center justify-center text-[var(--ink-muted)] hover:text-[var(--ink-primary)] hover:bg-[var(--bg-hover)]"
                    >
                      <Edit3 className="w-3.5 h-3.5" />
                    </button>
                  )}
                  {onDeleteProfile && (
                    <button
                      type="button"
                      disabled={inUse}
                      onClick={() => void onDeleteProfile(normKey)}
                      aria-label={`Delete profile ${normKey}`}
                      title={
                        inUse
                          ? "Reassign roles using this profile before deleting"
                          : `Delete ${normKey}`
                      }
                      className="h-7 w-7 rounded-lg flex items-center justify-center text-[var(--ink-muted)] hover:text-[var(--status-danger)] hover:bg-[var(--bg-hover)] disabled:opacity-30"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Default Role Assignments */}
      <section aria-label="Role assignments">
        <h2 className="text-[12px] font-semibold text-[var(--ink-primary)] mb-2">
          Default Role Assignments
        </h2>
        <div
          className="rounded-xl border divide-y"
          style={{
            backgroundColor: "var(--bg-surface)",
            borderColor: "var(--border-hairline)",
          }}
        >
          {roles.map((role) => {
            const currentRef = normalizeProfileKey(models.assignments.roles[role]);
            const family = resolveProfileFamily(models, currentRef);
            const isCollision = diversity.sameFamilyRoles.includes(role);
            return (
              <div
                key={role}
                className="px-3 py-2 flex items-center justify-between gap-2"
                style={{ borderColor: "var(--border-hairline)" }}
              >
                <div className="min-w-0">
                  <label
                    htmlFor={`role-select-${role}`}
                    className="text-[12.5px] font-medium text-[var(--ink-primary)] block"
                  >
                    {role}
                  </label>
                  <span
                    className="text-[10.5px] font-mono"
                    style={{
                      color: isCollision ? "var(--status-danger)" : "var(--ink-muted)",
                    }}
                  >
                    {family}
                  </span>
                </div>

                <select
                  id={`role-select-${role}`}
                  value={currentRef}
                  onChange={(e) => void onUpdateRole(role, e.target.value)}
                  className="h-7 rounded-[6px] px-2 font-mono text-[11px] max-w-[215px] truncate hover:bg-[var(--bg-hover)]"
                  style={{
                    backgroundColor: "transparent",
                    color: "var(--ink-primary)",
                  }}
                >
                  {profileKeys.map((pk) => {
                    const normKey = normalizeProfileKey(pk);
                    return (
                      <option
                        key={normKey}
                        value={normKey}
                        style={{ backgroundColor: "var(--bg-surface)" }}
                      >
                        {normKey} · {models.profiles[pk].model}
                      </option>
                    );
                  })}
                </select>
              </div>
            );
          })}
        </div>
      </section>

      {dataPolicy && (
        <section aria-label="Data policy">
          <h2 className="text-[12px] font-semibold text-[var(--ink-primary)] mb-2">
            Data Policy
          </h2>
          <div
            className="rounded-xl border divide-y text-[12.5px]"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--border-hairline)",
            }}
          >
            <label
              className="px-3 py-2.5 flex items-center justify-between gap-3 cursor-pointer"
              style={{ borderColor: "var(--border-hairline)" }}
            >
              <span className="text-[var(--ink-primary)]">Allow free-tier APIs</span>
              <input
                type="checkbox"
                checked={dataPolicy.allow_free_tier}
                onChange={(e) =>
                  void onUpdateDataPolicy({ allow_free_tier: e.target.checked })
                }
                className="w-4 h-4"
              />
            </label>
            <label
              className="px-3 py-2.5 flex items-center justify-between gap-3 cursor-pointer"
              style={{ borderColor: "var(--border-hairline)" }}
            >
              <span className="text-[var(--ink-primary)]">Allow redacted trace export</span>
              <input
                type="checkbox"
                checked={dataPolicy.allow_trace_export}
                onChange={(e) =>
                  void onUpdateDataPolicy({ allow_trace_export: e.target.checked })
                }
                className="w-4 h-4"
              />
            </label>
          </div>
        </section>
      )}

      <details className="group">
        <summary className="list-none cursor-pointer flex items-center gap-1.5 text-[12px] font-medium text-[var(--ink-muted)] hover:text-[var(--ink-primary)]">
          <ChevronRight className="w-3.5 h-3.5 transition-transform group-open:rotate-90" />
          Configured SDK providers ({providers.length})
        </summary>
        <div
          className="mt-2 rounded-xl border divide-y font-mono text-[11px]"
          style={{
            backgroundColor: "var(--bg-surface)",
            borderColor: "var(--border-hairline)",
          }}
        >
          {providers.map(([key, cfg]) => (
            <div
              key={key}
              className="px-3 py-1.5 flex items-center justify-between gap-2"
              style={{ borderColor: "var(--border-hairline)" }}
            >
              <span className="text-[var(--ink-primary)]">{key}</span>
              <span className="text-[var(--ink-muted)]">{cfg.billing_mode}</span>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
