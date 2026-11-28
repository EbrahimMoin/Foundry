import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Lock,
  Play,
  RefreshCw,
  Terminal,
  Trash2,
} from "lucide-react";
import type {
  ApiKeysStatusResponse,
  KeyModelsResponse,
  OpenAISubscriptionStatus,
  ProviderCatalogEntry,
  TestConnectionResponse,
} from "../types";

interface ApiKeysPanelProps {
  apiKeys: ApiKeysStatusResponse | null;
  catalog: ProviderCatalogEntry[];
  openAiSub: OpenAISubscriptionStatus | null;
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
  onOpenModels?: () => void;
}

export function ApiKeysPanel({
  apiKeys,
  catalog,
  openAiSub,
  onUnlockVault,
  onSaveApiKey,
  onDeleteApiKey,
  onFetchKeyModels,
  onToggleOpenAiSub,
  onTestConnection,
  onOpenModels,
}: ApiKeysPanelProps) {
  const [vaultPassphrase, setVaultPassphrase] = useState("");
  const [keyName, setKeyName] = useState("API_KEY_1");
  const [keyProvider, setKeyProvider] = useState("gemini");
  const [keySecret, setKeySecret] = useState("");
  const [showKeySecret, setShowKeySecret] = useState(false);
  const [busy, setBusy] = useState(false);

  const [testingTarget, setTestingTarget] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestConnectionResponse>>({});
  const [modelSyncNote, setModelSyncNote] = useState<Record<string, string>>({});
  const [subTestModel, setSubTestModel] = useState<string>(
    openAiSub?.models[0] ?? "gpt-5"
  );

  const keyProviders = catalog.filter((p) => p.id !== "openai_codex_cli");
  const normalizedKeyPreview =
    keyName.trim().replace(/[\s-]+/g, "_").toUpperCase() || "API_KEY_1";

  const handleTestKey = async (keyVar: string, providerId: string) => {
    const cat = catalog.find((c) => c.id === providerId);
    const defaultModel = cat?.models[0] ?? "gpt-4.1-mini";
    setTestingTarget(keyVar);
    try {
      const res = await onTestConnection({
        auth_source: "api_key",
        api_key_var: keyVar,
        model: defaultModel,
      });
      setTestResults((prev) => ({ ...prev, [keyVar]: res }));
    } finally {
      setTestingTarget(null);
    }
  };

  const handleFetchModels = async (keyVar: string) => {
    setTestingTarget(`models:${keyVar}`);
    try {
      const res = await onFetchKeyModels(keyVar);
      setModelSyncNote((prev) => ({
        ...prev,
        [keyVar]: `${res.models.length} models (${res.source === "live_api" ? "live API" : "catalog"})`,
      }));
    } finally {
      setTestingTarget(null);
    }
  };

  const handleTestSubscription = async () => {
    setTestingTarget("openai_subscription");
    try {
      const res = await onTestConnection({
        auth_source: "openai_subscription",
        model: subTestModel,
      });
      setTestResults((prev) => ({ ...prev, openai_subscription: res }));
    } finally {
      setTestingTarget(null);
    }
  };

  return (
    <div className="p-4 space-y-5 overflow-y-auto h-full">
      <div className="flex items-start justify-between gap-2 text-[12px] text-[var(--ink-secondary)]">
        <p className="leading-relaxed">
          Save official API keys as named variables (e.g.{" "}
          <code className="font-mono text-[11px]">API_KEY_1</code>), then pick a key &amp; matching
          models on any agent or model profile.
        </p>
        {onOpenModels && (
          <button
            type="button"
            onClick={onOpenModels}
            className="text-[11.5px] font-medium shrink-0 hover:underline"
            style={{ color: "var(--accent)" }}
          >
            Models →
          </button>
        )}
      </div>

      {/* Vault Unlock Notice when headless encrypted_vault is locked */}
      {apiKeys && apiKeys.backend === "encrypted_vault" && !apiKeys.vault_unlocked && (
        <div
          className="rounded-xl border p-3.5 space-y-2.5"
          style={{
            backgroundColor: "var(--status-warn-bg)",
            borderColor: "var(--status-warn-border)",
          }}
        >
          <div className="flex items-center gap-2 text-[12.5px] font-semibold text-[var(--ink-primary)]">
            <Lock className="w-3.5 h-3.5" style={{ color: "var(--status-warn)" }} />
            Unlock Encrypted Local Vault
          </div>
          <p className="text-[11.5px] text-[var(--ink-secondary)]">
            Passphrase stays in daemon RAM only for storing or calling vaulted keys.
          </p>
          <div className="flex items-center gap-2">
            <input
              type="password"
              value={vaultPassphrase}
              onChange={(e) => setVaultPassphrase(e.target.value)}
              placeholder="Vault passphrase…"
              aria-label="Vault passphrase"
              className="flex-1 h-8 px-2.5 rounded-lg border text-[12px]"
              style={{
                backgroundColor: "var(--bg-surface)",
                borderColor: "var(--border-hairline)",
                color: "var(--ink-primary)",
              }}
            />
            <button
              type="button"
              disabled={!vaultPassphrase || busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await onUnlockVault(vaultPassphrase);
                  setVaultPassphrase("");
                } finally {
                  setBusy(false);
                }
              }}
              className="h-8 px-3 rounded-lg text-[12px] font-medium disabled:opacity-50"
              style={{ backgroundColor: "var(--accent)", color: "#ffffff" }}
            >
              Unlock
            </button>
          </div>
        </div>
      )}

      {/* Add / Update Named API Key Form */}
      <section
        aria-label="Add API key variable"
        className="rounded-xl border p-3.5 space-y-3"
        style={{
          backgroundColor: "var(--bg-surface)",
          borderColor: "var(--border-hairline)",
        }}
      >
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-[12.5px] font-semibold text-[var(--ink-primary)] flex items-center gap-1.5">
            <KeyRound className="w-3.5 h-3.5" style={{ color: "var(--accent)" }} />
            Add / Update API Key Variable
          </h3>
          <span className="font-mono text-[10.5px] text-[var(--accent)]">
            {normalizedKeyPreview}
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          <label className="space-y-1">
            <span className="text-[11px] text-[var(--ink-muted)] block">Variable Name</span>
            <input
              type="text"
              value={keyName}
              onChange={(e) => setKeyName(e.target.value)}
              placeholder="API_KEY_1"
              className="w-full h-8 px-2.5 rounded-lg border font-mono text-[12px]"
              style={{
                backgroundColor: "var(--bg-canvas)",
                borderColor: "var(--border-hairline)",
                color: "var(--ink-primary)",
              }}
            />
          </label>

          <label className="space-y-1">
            <span className="text-[11px] text-[var(--ink-muted)] block">Provider</span>
            <select
              aria-label="Provider"
              value={keyProvider}
              onChange={(e) => setKeyProvider(e.target.value)}
              className="w-full h-8 px-2 rounded-lg border text-[12px]"
              style={{
                backgroundColor: "var(--bg-canvas)",
                borderColor: "var(--border-hairline)",
                color: "var(--ink-primary)",
              }}
            >
              {keyProviders.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <input
              type={showKeySecret ? "text" : "password"}
              value={keySecret}
              onChange={(e) => setKeySecret(e.target.value)}
              placeholder="Paste official API key secret…"
              aria-label="API key secret value"
              className="w-full h-8 pl-2.5 pr-8 rounded-lg border font-mono text-[12px]"
              style={{
                backgroundColor: "var(--bg-canvas)",
                borderColor: "var(--border-hairline)",
                color: "var(--ink-primary)",
              }}
            />
            <button
              type="button"
              onClick={() => setShowKeySecret((prev) => !prev)}
              aria-label={showKeySecret ? "Hide key" : "Show key"}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--ink-muted)] hover:text-[var(--ink-primary)]"
            >
              {showKeySecret ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
            </button>
          </div>

          <button
            type="button"
            disabled={!keyName.trim() || !keySecret.trim() || busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onSaveApiKey({
                  name: keyName,
                  secret: keySecret,
                  provider: keyProvider,
                  passphrase: vaultPassphrase || undefined,
                });
                setKeySecret("");
              } finally {
                setBusy(false);
              }
            }}
            className="h-8 px-3.5 rounded-lg text-[12px] font-medium disabled:opacity-45 shrink-0"
            style={{ backgroundColor: "var(--accent)", color: "#ffffff" }}
          >
            Save Key
          </button>
        </div>
      </section>

      {/* Saved API Keys */}
      <section aria-label="Saved API keys" className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-[12px] font-semibold text-[var(--ink-primary)]">
            Saved API Keys ({apiKeys?.keys.length ?? 0})
          </h3>
          <span className="font-mono text-[10.5px] text-[var(--ink-muted)]">
            {apiKeys?.backend ?? "encrypted_vault"}
          </span>
        </div>

        {!apiKeys || apiKeys.keys.length === 0 ? (
          <div
            className="rounded-xl border p-3.5 text-[12px] text-[var(--ink-muted)]"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--border-hairline)",
            }}
          >
            No named API keys saved yet.
          </div>
        ) : (
          <div className="space-y-2">
            {apiKeys.keys.map((entry) => {
              const testRes = testResults[entry.name];
              const syncNote = modelSyncNote[entry.name];
              const isTesting = testingTarget === entry.name;
              const isSyncing = testingTarget === `models:${entry.name}`;
              return (
                <div
                  key={entry.name}
                  className="rounded-xl border p-3 space-y-2"
                  style={{
                    backgroundColor: "var(--bg-surface)",
                    borderColor: "var(--border-hairline)",
                  }}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-mono text-[12px] font-semibold text-[var(--ink-primary)]">
                          {entry.name}
                        </span>
                        <span
                          className="px-2 py-0.5 rounded-full text-[10.5px] font-medium"
                          style={{
                            backgroundColor: "var(--accent-soft)",
                            color: "var(--accent)",
                          }}
                        >
                          {entry.provider_label || entry.provider}
                        </span>
                        <span className="font-mono text-[10.5px] text-[var(--ink-muted)]">
                          {entry.masked_preview}
                        </span>
                      </div>
                      <div className="text-[11px] text-[var(--ink-muted)] mt-0.5 truncate">
                        {entry.assigned_agents.length > 0
                          ? `Bound to: ${entry.assigned_agents.join(", ")}`
                          : "Unassigned — select on any agent or model profile"}
                      </div>
                    </div>

                    <button
                      type="button"
                      disabled={busy}
                      onClick={async () => {
                        setBusy(true);
                        try {
                          await onDeleteApiKey(entry.name);
                        } finally {
                          setBusy(false);
                        }
                      }}
                      aria-label={`Delete ${entry.name}`}
                      className="h-7 w-7 rounded-lg flex items-center justify-center text-[var(--ink-muted)] hover:text-[var(--status-danger)] hover:bg-[var(--bg-hover)] shrink-0"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5">
                    <button
                      type="button"
                      disabled={isTesting}
                      onClick={() => void handleTestKey(entry.name, entry.provider)}
                      className="h-7 px-2.5 rounded-lg border inline-flex items-center gap-1 text-[11px] font-medium hover:bg-[var(--bg-hover)] disabled:opacity-50"
                      style={{
                        borderColor: "var(--border-hairline)",
                        color: "var(--ink-primary)",
                      }}
                    >
                      <Play className="w-3 h-3" style={{ color: "var(--accent)" }} />
                      {isTesting ? "Testing…" : "Test Key"}
                    </button>
                    <button
                      type="button"
                      disabled={isSyncing}
                      onClick={() => void handleFetchModels(entry.name)}
                      className="h-7 px-2.5 rounded-lg border inline-flex items-center gap-1 text-[11px] font-medium hover:bg-[var(--bg-hover)] disabled:opacity-50"
                      style={{
                        borderColor: "var(--border-hairline)",
                        color: "var(--ink-secondary)",
                      }}
                    >
                      <RefreshCw className={`w-3 h-3 ${isSyncing ? "animate-spin" : ""}`} />
                      Fetch Models
                    </button>
                    {syncNote && (
                      <span className="font-mono text-[10.5px] text-[var(--ink-muted)]">
                        {syncNote}
                      </span>
                    )}
                  </div>

                  {testRes && (
                    <div
                      className="rounded-lg border px-2.5 py-1.5 text-[11px] flex items-start gap-1.5"
                      style={
                        testRes.ok
                          ? {
                              backgroundColor: "var(--status-nominal-bg)",
                              borderColor: "var(--status-nominal-border)",
                              color: "var(--ink-primary)",
                            }
                          : {
                              backgroundColor: "var(--status-danger-bg)",
                              borderColor: "var(--status-danger-border)",
                              color: "var(--ink-primary)",
                            }
                      }
                    >
                      {testRes.ok ? (
                        <CheckCircle2
                          className="w-3.5 h-3.5 shrink-0 mt-0.5"
                          style={{ color: "var(--status-nominal)" }}
                        />
                      ) : (
                        <AlertTriangle
                          className="w-3.5 h-3.5 shrink-0 mt-0.5"
                          style={{ color: "var(--status-danger)" }}
                        />
                      )}
                      <div className="min-w-0">
                        <div className="font-medium">
                          {testRes.ok
                            ? `${testRes.model} OK (${testRes.latency_ms} ms)`
                            : `Failed (${testRes.model})`}
                        </div>
                        <div className="text-[10.5px] text-[var(--ink-secondary)] break-words">
                          {testRes.ok ? testRes.reply_preview : testRes.error}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* OpenAI Subscription via Opt-In Single-Shot Official Codex CLI (A7-2) */}
      <section
        aria-label="OpenAI Subscription CLI"
        className="rounded-xl border p-3.5 space-y-3"
        style={{
          backgroundColor: "var(--bg-surface)",
          borderColor: "var(--border-hairline)",
        }}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="flex items-center gap-1.5">
              <Terminal className="w-3.5 h-3.5" style={{ color: "var(--accent)" }} />
              <h3 className="text-[12.5px] font-semibold text-[var(--ink-primary)]">
                OpenAI Subscription (Codex CLI)
              </h3>
            </div>
            <p className="text-[11px] text-[var(--ink-muted)] mt-0.5 leading-snug">
              {openAiSub?.policy_note ??
                "Opt-in single-shot non-tool execution via official `codex exec`. Never reads ~/.codex tokens."}
            </p>
          </div>
          <span
            className="px-2 py-0.5 rounded-full font-mono text-[10px] shrink-0"
            style={
              openAiSub?.cli_installed
                ? {
                    backgroundColor: "var(--status-nominal-bg)",
                    color: "var(--status-nominal)",
                  }
                : {
                    backgroundColor: "var(--bg-active)",
                    color: "var(--ink-muted)",
                  }
            }
          >
            {openAiSub?.cli_installed
              ? openAiSub.cli_version || "codex ready"
              : "CLI not found"}
          </span>
        </div>

        {openAiSub && !openAiSub.cli_installed && (
          <div
            className="rounded-lg border px-2.5 py-2 text-[11px] text-[var(--ink-secondary)] font-mono"
            style={{
              backgroundColor: "var(--bg-canvas)",
              borderColor: "var(--border-hairline)",
            }}
          >
            {openAiSub.setup_hint}
          </div>
        )}

        <label className="flex items-center justify-between gap-3 cursor-pointer text-[12px]">
          <div>
            <span className="font-medium text-[var(--ink-primary)] block">
              Enable OpenAI Subscription mode
            </span>
            <span className="text-[11px] text-[var(--ink-muted)]">
              Single-shot chats &amp; plans only; tool-calling agents require an API key
            </span>
          </div>
          <input
            type="checkbox"
            checked={Boolean(openAiSub?.enabled)}
            onChange={(e) => void onToggleOpenAiSub(e.target.checked)}
            className="w-4 h-4 shrink-0"
          />
        </label>

        <div className="flex items-center gap-2 pt-0.5">
          <select
            aria-label="OpenAI Subscription test model"
            value={subTestModel}
            onChange={(e) => setSubTestModel(e.target.value)}
            className="flex-1 h-8 px-2 rounded-lg border font-mono text-[11.5px]"
            style={{
              backgroundColor: "var(--bg-canvas)",
              borderColor: "var(--border-hairline)",
              color: "var(--ink-primary)",
            }}
          >
            {(openAiSub?.models ?? ["gpt-5", "gpt-5-codex", "o4-mini", "gpt-4.1"]).map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>

          <button
            type="button"
            disabled={testingTarget === "openai_subscription"}
            onClick={() => void handleTestSubscription()}
            className="h-8 px-3 rounded-lg border inline-flex items-center gap-1.5 text-[11.5px] font-medium hover:bg-[var(--bg-hover)] disabled:opacity-50 shrink-0"
            style={{
              borderColor: "var(--border-hairline)",
              color: "var(--ink-primary)",
            }}
          >
            <Play className="w-3 h-3" style={{ color: "var(--accent)" }} />
            {testingTarget === "openai_subscription" ? "Testing…" : "Test CLI"}
          </button>
        </div>

        {testResults.openai_subscription && (
          <div
            className="rounded-lg border px-2.5 py-1.5 text-[11px] flex items-start gap-1.5"
            style={
              testResults.openai_subscription.ok
                ? {
                    backgroundColor: "var(--status-nominal-bg)",
                    borderColor: "var(--status-nominal-border)",
                    color: "var(--ink-primary)",
                  }
                : {
                    backgroundColor: "var(--status-danger-bg)",
                    borderColor: "var(--status-danger-border)",
                    color: "var(--ink-primary)",
                  }
            }
          >
            {testResults.openai_subscription.ok ? (
              <CheckCircle2
                className="w-3.5 h-3.5 shrink-0 mt-0.5"
                style={{ color: "var(--status-nominal)" }}
              />
            ) : (
              <AlertTriangle
                className="w-3.5 h-3.5 shrink-0 mt-0.5"
                style={{ color: "var(--status-danger)" }}
              />
            )}
            <div className="min-w-0">
              <div className="font-medium">
                {testResults.openai_subscription.ok
                  ? `Subscription CLI OK (${testResults.openai_subscription.latency_ms} ms)`
                  : "Subscription CLI check failed"}
              </div>
              <div className="text-[10.5px] text-[var(--ink-secondary)] break-words">
                {testResults.openai_subscription.ok
                  ? testResults.openai_subscription.reply_preview
                  : testResults.openai_subscription.error}
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
