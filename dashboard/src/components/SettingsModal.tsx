import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Laptop,
  Moon,
  RefreshCw,
  Shield,
  Sliders,
  Sparkles,
  Sun,
  X,
} from "lucide-react";
import type { ResolvedTheme, ThemePreference } from "../hooks/useTheme";
import type {
  AutonomyConfig,
  AutonomyOverrides,
  DataPolicyConfig,
  DecisionModeOption,
  ModelsConfig,
  RuntimeSettingsResponse,
} from "../types";
import {
  checkVerifierDiversity,
  normalizeProfileKey,
  resolveProfileFamily,
} from "../utils/foundryMetrics";

export type SettingsTab = "general" | "autonomy" | "models" | "privacy";

interface SettingsModalProps {
  open: boolean;
  onClose: () => void;
  themePreference: ThemePreference;
  systemTheme: ResolvedTheme;
  onChangeThemePreference: (pref: ThemePreference) => void;
  sidebarCollapsed: boolean;
  onToggleSidebarCollapsed: () => void;
  daemonOnline: boolean;
  demoMode: boolean;
  onToggleDemoMode: (enabled: boolean) => void;
  onReconnectDaemon: () => Promise<boolean>;
  autonomy: AutonomyConfig | null;
  onUpdateAutonomy: (patch: {
    preset?: AutonomyConfig["preset"];
    overrides?: AutonomyOverrides;
    confidence_threshold?: number;
  }) => Promise<void>;
  models: ModelsConfig | null;
  onUpdateRole: (role: string, profile: string) => Promise<void>;
  dataPolicy: DataPolicyConfig | null;
  onUpdateDataPolicy: (patch: Partial<DataPolicyConfig>) => Promise<void>;
  runtimeSettings: RuntimeSettingsResponse | null;
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

const OVERRIDE_CATEGORIES: Array<{
  key: keyof AutonomyOverrides;
  label: string;
  hint: string;
}> = [
  { key: "strategic", label: "Strategic", hint: "Architecture, dependencies, scope plans" },
  { key: "tactical", label: "Tactical", hint: "Contracts, reopens, non-breaking schema" },
  { key: "operational", label: "Operational", hint: "Routine task retries and scheduling" },
  { key: "release_infra", label: "Release & Infra", hint: "Blue/green promotion, DB migrations" },
];

const PRESET_OPTIONS: Array<{
  id: AutonomyConfig["preset"];
  label: string;
  desc: string;
}> = [
  { id: "autopilot", label: "Autopilot", desc: "Auto-approve reversible choices above threshold" },
  { id: "balanced", label: "Balanced", desc: "Sign off on contracts, security & releases" },
  { id: "architect", label: "Architect", desc: "Review strategic & tactical decisions" },
  { id: "full_control", label: "Full Control", desc: "Human approval required on every gate" },
  { id: "benchmark", label: "Benchmark", desc: "Pinned non-interactive benchmark profile" },
];

export function SettingsModal({
  open,
  onClose,
  themePreference,
  systemTheme,
  onChangeThemePreference,
  sidebarCollapsed,
  onToggleSidebarCollapsed,
  daemonOnline,
  demoMode,
  onToggleDemoMode,
  onReconnectDaemon,
  autonomy,
  onUpdateAutonomy,
  models,
  onUpdateRole,
  dataPolicy,
  onUpdateDataPolicy,
  runtimeSettings,
}: SettingsModalProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>("general");
  const [thresholdDraft, setThresholdDraft] = useState<number>(
    autonomy?.confidence_threshold ?? 0.6
  );
  const [savingLabel, setSavingLabel] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const dialogRef = useRef<HTMLDialogElement | null>(null);

  useEffect(() => {
    if (autonomy) {
      setThresholdDraft(autonomy.confidence_threshold);
    }
  }, [autonomy]);

  // Open/close native modal top layer
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open) {
      if (!dialog.open) dialog.showModal();
    } else if (dialog.open) {
      dialog.close();
    }
  }, [open]);

  // Mandatory Safari light-dismiss fallback for browsers lacking <dialog closedby>
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if ("closedBy" in HTMLDialogElement.prototype) return;

    const handleBackdropClick = (event: MouseEvent) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      const isDialogContent =
        rect.top <= event.clientY &&
        event.clientY <= rect.top + rect.height &&
        rect.left <= event.clientX &&
        event.clientX <= rect.left + rect.width;
      if (!isDialogContent) {
        dialog.close();
      }
    };

    dialog.addEventListener("click", handleBackdropClick);
    return () => dialog.removeEventListener("click", handleBackdropClick);
  }, []);

  const flashSaved = (msg: string) => {
    setSavingLabel(msg);
    window.setTimeout(() => setSavingLabel(null), 1800);
  };

  const navItems: Array<{ key: SettingsTab; label: string; icon: typeof Sliders }> = [
    { key: "general", label: "General", icon: Sliders },
    { key: "autonomy", label: "Autonomy", icon: Sparkles },
    { key: "models", label: "Models", icon: Bot },
    { key: "privacy", label: "Privacy & Runtime", icon: Shield },
  ];

  const diversity = models ? checkVerifierDiversity(models) : null;
  const profileEntries = models ? Object.entries(models.profiles) : [];
  const roleKeys = models
    ? Object.keys(models.assignments.roles).sort((a, b) => {
        const ia = ROLE_ORDER.indexOf(a);
        const ib = ROLE_ORDER.indexOf(b);
        return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
      })
    : [];

  return (
    <dialog
      ref={dialogRef}
      closedby="any"
      aria-labelledby="settings-modal-title"
      className="foundry-dialog"
      onClose={onClose}
    >
      <div className="flex flex-col sm:flex-row h-[min(560px,84vh)] w-full">
        {/* Left Navigation Column (Claude-style settings rail) */}
        <aside
          className="sm:w-[196px] shrink-0 border-b sm:border-b-0 sm:border-r flex flex-col justify-between p-4"
          style={{
            backgroundColor: "var(--bg-sidebar)",
            borderColor: "var(--border-hairline)",
          }}
        >
          <div>
            <div className="flex items-center justify-between sm:block mb-3 sm:mb-4 px-1.5">
              <h2
                id="settings-modal-title"
                className="font-serif text-[21px] font-medium tracking-tight text-[var(--ink-primary)]"
              >
                Settings
              </h2>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close settings"
                className="sm:hidden h-8 w-8 rounded-full flex items-center justify-center text-[var(--ink-secondary)] hover:bg-[var(--bg-hover)]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <nav
              className="flex sm:flex-col gap-1 overflow-x-auto sm:overflow-visible"
              aria-label="Settings categories"
            >
              {navItems.map((item) => {
                const Icon = item.icon;
                const active = activeTab === item.key;
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setActiveTab(item.key)}
                    aria-current={active ? "page" : undefined}
                    className="h-9 px-3 rounded-xl flex items-center gap-2.5 text-[13px] shrink-0 transition-colors text-left"
                    style={{
                      backgroundColor: active ? "var(--bg-active)" : "transparent",
                      color: active ? "var(--ink-primary)" : "var(--ink-secondary)",
                      fontWeight: active ? 500 : 400,
                    }}
                  >
                    <Icon className="w-[15px] h-[15px] shrink-0" />
                    <span className="truncate">{item.label}</span>
                  </button>
                );
              })}
            </nav>
          </div>

          <div className="hidden sm:flex items-center justify-between px-2 pt-3 text-[11px] text-[var(--ink-muted)]">
            <span className="font-mono">Ctrl+,</span>
            <span>Esc to close</span>
          </div>
        </aside>

        {/* Right Detail Pane */}
        <section className="flex-1 min-w-0 min-h-0 flex flex-col">
          <header
            className="px-6 py-4 border-b flex items-center justify-between gap-3 shrink-0"
            style={{ borderColor: "var(--border-hairline)" }}
          >
            <div>
              <h3 className="text-[15px] font-semibold text-[var(--ink-primary)]">
                {navItems.find((n) => n.key === activeTab)?.label}
              </h3>
              {savingLabel && (
                <p className="text-[11.5px] mt-0.5" style={{ color: "var(--accent)" }}>
                  {savingLabel}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close settings"
              className="hidden sm:flex h-8 w-8 rounded-full items-center justify-center transition-colors hover:bg-[var(--bg-hover)] text-[var(--ink-secondary)]"
            >
              <X className="w-4 h-4" />
            </button>
          </header>

          <div className="flex-1 min-h-0 overflow-y-auto p-6 space-y-6">
            {/* TAB 1: GENERAL & APPEARANCE */}
            {activeTab === "general" && (
              <>
                <div className="space-y-3">
                  <div>
                    <h4 className="text-[13px] font-semibold text-[var(--ink-primary)]">
                      Appearance
                    </h4>
                    <p className="text-[12px] text-[var(--ink-muted)] mt-0.5">
                      Choose how Foundry renders chrome surfaces, scrollbars, and dialogs.
                    </p>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    {(
                      [
                        {
                          key: "system" as const,
                          label: "System",
                          sub: `Currently ${systemTheme}`,
                          icon: Laptop,
                        },
                        { key: "light" as const, label: "Light", sub: "Warm parchment", icon: Sun },
                        { key: "dark" as const, label: "Dark", sub: "Pine charcoal", icon: Moon },
                      ] as const
                    ).map((opt) => {
                      const Icon = opt.icon;
                      const active = themePreference === opt.key;
                      return (
                        <button
                          key={opt.key}
                          type="button"
                          onClick={() => onChangeThemePreference(opt.key)}
                          aria-pressed={active}
                          className="rounded-xl border p-3 text-left transition-colors"
                          style={{
                            backgroundColor: active ? "var(--bg-active)" : "var(--bg-canvas)",
                            borderColor: active ? "var(--accent)" : "var(--border-hairline)",
                          }}
                        >
                          <div className="flex items-center gap-2 text-[13px] font-medium text-[var(--ink-primary)]">
                            <Icon className="w-4 h-4" />
                            {opt.label}
                          </div>
                          <div className="text-[11.5px] text-[var(--ink-muted)] mt-1">{opt.sub}</div>
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div
                  className="rounded-xl border divide-y text-[13px]"
                  style={{
                    backgroundColor: "var(--bg-canvas)",
                    borderColor: "var(--border-hairline)",
                  }}
                >
                  <label
                    className="px-4 py-3 flex items-center justify-between gap-4 cursor-pointer"
                    style={{ borderColor: "var(--border-hairline)" }}
                  >
                    <div>
                      <div className="font-medium text-[var(--ink-primary)]">Compact sidebar rail</div>
                      <div className="text-[12px] text-[var(--ink-muted)]">
                        Collapse navigation into a 60px icon strip for wider split panes
                      </div>
                    </div>
                    <input
                      type="checkbox"
                      checked={sidebarCollapsed}
                      onChange={onToggleSidebarCollapsed}
                      className="w-4 h-4"
                    />
                  </label>

                  <label
                    className="px-4 py-3 flex items-center justify-between gap-4 cursor-pointer"
                    style={{ borderColor: "var(--border-hairline)" }}
                  >
                    <div>
                      <div className="font-medium text-[var(--ink-primary)]">
                        Preview demo dataset
                      </div>
                      <div className="text-[12px] text-[var(--ink-muted)]">
                        Inspect sample Wave 2 telemetry even while connected to a clean local daemon
                      </div>
                    </div>
                    <input
                      type="checkbox"
                      checked={demoMode}
                      disabled={!daemonOnline && demoMode}
                      onChange={(e) => onToggleDemoMode(e.target.checked)}
                      className="w-4 h-4"
                    />
                  </label>
                </div>

                <div
                  className="rounded-xl border p-4 flex items-center justify-between gap-3"
                  style={{
                    backgroundColor: "var(--bg-canvas)",
                    borderColor: "var(--border-hairline)",
                  }}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-[13px] font-medium text-[var(--ink-primary)]">
                      <span
                        className="w-2 h-2 rounded-full shrink-0"
                        style={{
                          backgroundColor: daemonOnline
                            ? "var(--status-nominal)"
                            : "var(--status-warn)",
                        }}
                      />
                      {daemonOnline
                        ? demoMode
                          ? "Daemon reachable · Viewing demo snapshot"
                          : "Connected to Foundry FastAPI daemon"
                        : "Daemon unreachable · Offline demo mode"}
                    </div>
                    <p className="text-[11.5px] font-mono text-[var(--ink-muted)] mt-1 truncate">
                      {runtimeSettings
                        ? `${runtimeSettings.server.host}:${runtimeSettings.server.port}`
                        : "127.0.0.1:4200"}
                    </p>
                  </div>

                  <button
                    type="button"
                    disabled={reconnecting}
                    onClick={async () => {
                      setReconnecting(true);
                      try {
                        const ok = await onReconnectDaemon();
                        flashSaved(ok ? "Daemon connected" : "Daemon offline");
                      } finally {
                        setReconnecting(false);
                      }
                    }}
                    className="h-8 px-3 rounded-lg border inline-flex items-center gap-1.5 text-[12px] font-medium hover:bg-[var(--bg-hover)] disabled:opacity-50 shrink-0"
                    style={{
                      borderColor: "var(--border-hairline)",
                      color: "var(--ink-primary)",
                    }}
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${reconnecting ? "animate-spin" : ""}`} />
                    Check Daemon
                  </button>
                </div>
              </>
            )}

            {/* TAB 2: AUTONOMY & GUARDRAILS */}
            {activeTab === "autonomy" && autonomy && (
              <>
                <div className="space-y-2.5">
                  <h4 className="text-[13px] font-semibold text-[var(--ink-primary)]">
                    Autonomy Preset
                  </h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {PRESET_OPTIONS.map((p) => {
                      const active = autonomy.preset === p.id;
                      return (
                        <button
                          key={p.id}
                          type="button"
                          onClick={async () => {
                            await onUpdateAutonomy({ preset: p.id });
                            flashSaved(`Preset saved (${p.id})`);
                          }}
                          aria-pressed={active}
                          className="rounded-xl border px-3.5 py-2.5 text-left transition-colors"
                          style={{
                            backgroundColor: active ? "var(--bg-active)" : "var(--bg-canvas)",
                            borderColor: active ? "var(--accent)" : "var(--border-hairline)",
                          }}
                        >
                          <div className="text-[13px] font-medium text-[var(--ink-primary)]">
                            {p.label}
                          </div>
                          <div className="text-[11.5px] text-[var(--ink-muted)] mt-0.5">
                            {p.desc}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between text-[13px]">
                    <label
                      htmlFor="confidence-slider"
                      className="font-semibold text-[var(--ink-primary)]"
                    >
                      Auto-Decision Confidence Threshold
                    </label>
                    <span className="font-mono text-[12px] tabular text-[var(--accent)]">
                      {thresholdDraft.toFixed(2)}
                    </span>
                  </div>
                  <input
                    id="confidence-slider"
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={thresholdDraft}
                    onChange={(e) => setThresholdDraft(Number(e.target.value))}
                    onMouseUp={async () => {
                      await onUpdateAutonomy({ confidence_threshold: thresholdDraft });
                      flashSaved("Threshold updated");
                    }}
                    onKeyUp={async () => {
                      await onUpdateAutonomy({ confidence_threshold: thresholdDraft });
                      flashSaved("Threshold updated");
                    }}
                    onTouchEnd={async () => {
                      await onUpdateAutonomy({ confidence_threshold: thresholdDraft });
                      flashSaved("Threshold updated");
                    }}
                    className="w-full"
                  />
                  <p className="text-[11.5px] text-[var(--ink-muted)]">
                    Decisions with confidence below {thresholdDraft.toFixed(2)} escalate to human
                    sign-off unless overridden below.
                  </p>
                </div>

                <div className="space-y-2">
                  <h4 className="text-[13px] font-semibold text-[var(--ink-primary)]">
                    Category Overrides
                  </h4>
                  <div
                    className="rounded-xl border divide-y"
                    style={{
                      backgroundColor: "var(--bg-canvas)",
                      borderColor: "var(--border-hairline)",
                    }}
                  >
                    {OVERRIDE_CATEGORIES.map((cat) => {
                      const val = autonomy.overrides?.[cat.key] ?? "";
                      return (
                        <div
                          key={cat.key}
                          className="px-4 py-2.5 flex items-center justify-between gap-3"
                          style={{ borderColor: "var(--border-hairline)" }}
                        >
                          <div className="min-w-0">
                            <div className="text-[13px] font-medium text-[var(--ink-primary)]">
                              {cat.label}
                            </div>
                            <div className="text-[11.5px] text-[var(--ink-muted)] truncate">
                              {cat.hint}
                            </div>
                          </div>
                          <select
                            aria-label={`${cat.label} decision mode`}
                            value={val}
                            onChange={async (e) => {
                              const raw = e.target.value;
                              const nextMode: DecisionModeOption | null =
                                raw === "" ? null : (raw as DecisionModeOption);
                              const nextOverrides: AutonomyOverrides = {
                                ...autonomy.overrides,
                                [cat.key]: nextMode,
                              };
                              await onUpdateAutonomy({ overrides: nextOverrides });
                              flashSaved(`${cat.label} mode saved`);
                            }}
                            className="h-8 rounded-lg border px-2.5 text-[12px]"
                            style={{
                              backgroundColor: "var(--bg-surface)",
                              borderColor: "var(--border-hairline)",
                              color: "var(--ink-primary)",
                            }}
                          >
                            <option value="">Inherit preset</option>
                            <option value="auto">Auto</option>
                            <option value="approve">Approve</option>
                            <option value="human">Human</option>
                          </select>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </>
            )}

            {/* TAB 3: MODELS & PROVIDERS */}
            {activeTab === "models" && models && (
              <>
                {diversity &&
                  (diversity.diverse ? (
                    <div
                      className="rounded-xl border px-3.5 py-2.5 flex items-center gap-2.5 text-[12.5px]"
                      style={{
                        backgroundColor: "var(--status-nominal-bg)",
                        borderColor: "var(--status-nominal-border)",
                        color: "var(--ink-primary)",
                      }}
                    >
                      <CheckCircle2
                        className="w-4 h-4 shrink-0"
                        style={{ color: "var(--status-nominal)" }}
                      />
                      <span>
                        Cross-family G3/G4 diversity verified ({diversity.implementerFamily} vs{" "}
                        {Array.from(new Set(diversity.verifierProfiles.map((v) => v.family))).join(
                          ", "
                        )}
                        )
                      </span>
                    </div>
                  ) : (
                    <div
                      className="rounded-xl border px-3.5 py-2.5 flex items-start gap-2.5 text-[12.5px]"
                      style={{
                        backgroundColor: "var(--status-danger-bg)",
                        borderColor: "var(--status-danger-border)",
                        color: "var(--ink-primary)",
                      }}
                    >
                      <AlertTriangle
                        className="w-4 h-4 shrink-0 mt-0.5"
                        style={{ color: "var(--status-danger)" }}
                      />
                      <span>
                        Verifier collision: {diversity.sameFamilyRoles.join(", ")} share family{" "}
                        <span className="font-mono">{diversity.implementerFamily}</span> with
                        implementer.
                      </span>
                    </div>
                  ))}

                <div className="space-y-2">
                  <h4 className="text-[13px] font-semibold text-[var(--ink-primary)]">
                    Agent Role Assignments (`models.yaml`)
                  </h4>
                  <div
                    className="rounded-xl border divide-y"
                    style={{
                      backgroundColor: "var(--bg-canvas)",
                      borderColor: "var(--border-hairline)",
                    }}
                  >
                    {roleKeys.map((role) => {
                      const currentRef = normalizeProfileKey(models.assignments.roles[role]);
                      const family = resolveProfileFamily(models, currentRef);
                      return (
                        <div
                          key={role}
                          className="px-4 py-2.5 flex items-center justify-between gap-3"
                          style={{ borderColor: "var(--border-hairline)" }}
                        >
                          <div className="min-w-0">
                            <div className="text-[13px] font-medium text-[var(--ink-primary)]">
                              {role}
                            </div>
                            <div className="font-mono text-[11px] text-[var(--ink-muted)]">
                              family: {family}
                            </div>
                          </div>
                          <select
                            aria-label={`Profile for ${role}`}
                            value={currentRef}
                            onChange={async (e) => {
                              await onUpdateRole(role, e.target.value);
                              flashSaved(`${role} assigned`);
                            }}
                            className="h-8 rounded-lg border px-2.5 font-mono text-[11.5px] max-w-[230px]"
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
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div className="space-y-2">
                  <h4 className="text-[13px] font-semibold text-[var(--ink-primary)]">
                    First-Party SDK Transports ({Object.keys(models.providers).length})
                  </h4>
                  <div
                    className="rounded-xl border divide-y font-mono text-[11.5px]"
                    style={{
                      backgroundColor: "var(--bg-canvas)",
                      borderColor: "var(--border-hairline)",
                    }}
                  >
                    {Object.entries(models.providers).map(([name, cfg]) => (
                      <div
                        key={name}
                        className="px-4 py-2 flex items-center justify-between gap-3"
                        style={{ borderColor: "var(--border-hairline)" }}
                      >
                        <div className="min-w-0">
                          <span className="text-[var(--ink-primary)] font-medium">{name}</span>
                          <span className="text-[var(--ink-muted)] ml-2">{cfg.transport}</span>
                        </div>
                        <span className="text-[var(--ink-secondary)] shrink-0">
                          {cfg.billing_mode} · {cfg.data_tier}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}

            {/* TAB 4: PRIVACY & RUNTIME INVARIANTS */}
            {activeTab === "privacy" && (
              <>
                {dataPolicy && (
                  <div className="space-y-2">
                    <h4 className="text-[13px] font-semibold text-[var(--ink-primary)]">
                      Data & Egress Policy
                    </h4>
                    <div
                      className="rounded-xl border divide-y text-[13px]"
                      style={{
                        backgroundColor: "var(--bg-canvas)",
                        borderColor: "var(--border-hairline)",
                      }}
                    >
                      <label
                        className="px-4 py-3 flex items-center justify-between gap-3 cursor-pointer"
                        style={{ borderColor: "var(--border-hairline)" }}
                      >
                        <div>
                          <div className="font-medium text-[var(--ink-primary)]">
                            Allow free-tier model APIs
                          </div>
                          <div className="text-[12px] text-[var(--ink-muted)]">
                            Disabled by default to block providers with training retention
                          </div>
                        </div>
                        <input
                          type="checkbox"
                          checked={dataPolicy.allow_free_tier}
                          onChange={async (e) => {
                            await onUpdateDataPolicy({ allow_free_tier: e.target.checked });
                            flashSaved("Data policy saved");
                          }}
                          className="w-4 h-4"
                        />
                      </label>

                      <label
                        className="px-4 py-3 flex items-center justify-between gap-3 cursor-pointer"
                        style={{ borderColor: "var(--border-hairline)" }}
                      >
                        <div>
                          <div className="font-medium text-[var(--ink-primary)]">
                            Allow redacted trace export
                          </div>
                          <div className="text-[12px] text-[var(--ink-muted)]">
                            Export secret-scrubbed traces for Phoenix / benchmark review
                          </div>
                        </div>
                        <input
                          type="checkbox"
                          checked={dataPolicy.allow_trace_export}
                          onChange={async (e) => {
                            await onUpdateDataPolicy({ allow_trace_export: e.target.checked });
                            flashSaved("Data policy saved");
                          }}
                          className="w-4 h-4"
                        />
                      </label>
                    </div>
                  </div>
                )}

                {runtimeSettings && (
                  <div className="space-y-2">
                    <h4 className="text-[13px] font-semibold text-[var(--ink-primary)]">
                      Runtime & Sandbox Guardrails (`foundry.yaml`, read-only)
                    </h4>
                    <div
                      className="rounded-xl border divide-y text-[12.5px]"
                      style={{
                        backgroundColor: "var(--bg-canvas)",
                        borderColor: "var(--border-hairline)",
                      }}
                    >
                      {[
                        {
                          label: "Heavy execution semaphore",
                          value: `${runtimeSettings.runtime.max_heavy_exec_slots} slots · ${runtimeSettings.runtime.sandbox_limits.memory} RAM / ${runtimeSettings.runtime.sandbox_limits.cpus} CPU`,
                        },
                        {
                          label: "Max concurrent task containers",
                          value: `${runtimeSettings.runtime.max_concurrent_tasks} containers (${runtimeSettings.runtime.sandbox_network})`,
                        },
                        {
                          label: "Local embeddings engine",
                          value: `${runtimeSettings.embeddings.provider} (${runtimeSettings.embeddings.model})`,
                        },
                        {
                          label: "Monthly global ceiling",
                          value: `$${runtimeSettings.budgets.monthly_usd.toFixed(2)} USD`,
                        },
                        {
                          label: "Secret storage order",
                          value: runtimeSettings.secrets_backends.join(" -> "),
                        },
                        {
                          label: "Package egress allowlist",
                          value: runtimeSettings.runtime.egress_proxy_allowlist.join(", "),
                        },
                      ].map((row) => (
                        <div
                          key={row.label}
                          className="px-4 py-2.5 flex items-center justify-between gap-4"
                          style={{ borderColor: "var(--border-hairline)" }}
                        >
                          <span className="text-[var(--ink-secondary)] shrink-0">{row.label}</span>
                          <span className="font-mono text-[11.5px] text-[var(--ink-primary)] text-right truncate">
                            {row.value}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </section>
      </div>
    </dialog>
  );
}
