import { AlertTriangle, CheckCircle2, ChevronRight, Settings } from "lucide-react";
import type { DataPolicyConfig, ModelsConfig } from "../types";
import {
  checkVerifierDiversity,
  normalizeProfileKey,
  resolveProfileFamily,
} from "../utils/foundryMetrics";

interface ModelSwitcherPanelProps {
  models: ModelsConfig | null;
  dataPolicy: DataPolicyConfig | null;
  onUpdateRole: (role: string, profile: string) => Promise<void>;
  onUpdateDataPolicy: (patch: Partial<DataPolicyConfig>) => Promise<void>;
  onOpenFullSettings?: () => void;
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

export function ModelSwitcherPanel({
  models,
  dataPolicy,
  onUpdateRole,
  onUpdateDataPolicy,
  onOpenFullSettings,
}: ModelSwitcherPanelProps) {
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
          {onOpenFullSettings && (
            <button
              type="button"
              onClick={onOpenFullSettings}
              className="inline-flex items-center gap-1 text-[11.5px] font-medium shrink-0 hover:underline"
              style={{ color: "var(--accent)" }}
            >
              <Settings className="w-3 h-3" />
              All settings
            </button>
          )}
        </div>
      ) : (
        <div
          className="rounded-[8px] border p-3 flex items-start gap-2.5 text-[12px]"
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

      <section aria-label="Role assignments">
        <h2 className="text-[11.5px] font-medium text-[var(--ink-muted)] mb-2">
          Role Assignments
        </h2>
        <div
          className="rounded-[8px] border divide-y"
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
                  className="h-7 rounded-[6px] px-2 font-mono text-[11px] max-w-[195px] truncate hover:bg-[var(--bg-hover)]"
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
          <h2 className="text-[11.5px] font-medium text-[var(--ink-muted)] mb-2">
            Data Policy
          </h2>
          <div
            className="rounded-[8px] border divide-y text-[12.5px]"
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
          className="mt-2 rounded-[8px] border divide-y font-mono text-[11px]"
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
