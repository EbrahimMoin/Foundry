import type { DataPolicyConfig, ModelsConfig } from "../types";

interface ModelSwitcherPanelProps {
  models: ModelsConfig | null;
  dataPolicy: DataPolicyConfig | null;
  onAssignRole: (role: string, profile: string) => void;
  onToggleDataPolicy: (policy: DataPolicyConfig) => void;
}

export function ModelSwitcherPanel({
  models,
  dataPolicy,
  onAssignRole,
  onToggleDataPolicy,
}: ModelSwitcherPanelProps) {
  if (!models) {
    return <p className="text-xs text-slate-500">Loading model configuration...</p>;
  }
  const profileNames = Object.keys(models.profiles);

  return (
    <div className="space-y-4 text-xs">
      <div>
        <h3 className="mb-2 font-semibold uppercase tracking-wider text-slate-400">
          Role Assignments
        </h3>
        <div className="space-y-2">
          {Object.entries(models.assignments.roles).map(([role, profile]) => (
            <div
              key={role}
              className="flex items-center justify-between gap-2 rounded border border-panelBorder bg-obsidian px-2.5 py-1.5"
            >
              <span className="font-medium text-slate-200">{role}</span>
              <select
                value={profile}
                onChange={(e) => onAssignRole(role, e.target.value)}
                className="rounded border border-slate-700 bg-panel px-2 py-1 font-mono text-[11px] text-indigo-300"
              >
                {profileNames.map((p) => (
                  <option key={p} value={p}>
                    {p} ({models.profiles[p].model})
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      </div>

      {dataPolicy && (
        <div className="space-y-2 border-t border-panelBorder pt-3">
          <h3 className="font-semibold uppercase tracking-wider text-slate-400">Data Policy</h3>
          <label className="flex items-center justify-between rounded border border-panelBorder bg-obsidian px-2.5 py-1.5">
            <span>Allow free-tier APIs</span>
            <input
              type="checkbox"
              checked={dataPolicy.allow_free_tier}
              onChange={(e) =>
                onToggleDataPolicy({ ...dataPolicy, allow_free_tier: e.target.checked })
              }
            />
          </label>
          <label className="flex items-center justify-between rounded border border-panelBorder bg-obsidian px-2.5 py-1.5">
            <span>Allow redacted trace export</span>
            <input
              type="checkbox"
              checked={dataPolicy.allow_trace_export}
              onChange={(e) =>
                onToggleDataPolicy({ ...dataPolicy, allow_trace_export: e.target.checked })
              }
            />
          </label>
        </div>
      )}

      <div className="space-y-1.5 border-t border-panelBorder pt-3">
        <h3 className="font-semibold uppercase tracking-wider text-slate-400">
          First-Party SDK Providers ({Object.keys(models.providers).length})
        </h3>
        {Object.entries(models.providers).map(([id, p]) => (
          <div
            key={id}
            className="flex items-center justify-between rounded bg-obsidian/70 px-2 py-1 text-[11px]"
          >
            <span className="font-mono text-slate-200">{id}</span>
            <span className="text-slate-400">
              {p.transport} · {p.billing_mode}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
