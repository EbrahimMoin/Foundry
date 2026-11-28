import { useState } from "react";
import { CostsMetricsPanel } from "./CostsMetricsPanel";
import { ModelSwitcherPanel } from "./ModelSwitcherPanel";
import type {
  CostsSummaryResponse,
  DataPolicyConfig,
  MetricsSummaryResponse,
  ModelsConfig,
} from "../types";

interface TelemetryHubProps {
  models: ModelsConfig | null;
  dataPolicy: DataPolicyConfig | null;
  costs: CostsSummaryResponse | null;
  metrics: MetricsSummaryResponse | null;
  onUpdateRole: (role: string, profile: string) => Promise<void>;
  onUpdateDataPolicy: (patch: Partial<DataPolicyConfig>) => Promise<void>;
  onOpenFullSettings: () => void;
}

export function TelemetryHub({
  models,
  dataPolicy,
  costs,
  metrics,
  onUpdateRole,
  onUpdateDataPolicy,
  onOpenFullSettings,
}: TelemetryHubProps) {
  const [tab, setTab] = useState<"costs" | "models">("costs");

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="shrink-0 px-6 sm:px-10 pt-8 pb-4">
        <div className="max-w-[660px] mx-auto">
          <h1 className="font-serif text-[32px] font-medium tracking-tight text-[var(--ink-primary)]">
            Telemetry
          </h1>
          <div className="mt-4 flex items-center gap-2" role="tablist">
            {(
              [
                { key: "costs", label: "Costs & Health" },
                { key: "models", label: "Models & Policy" },
              ] as const
            ).map((item) => {
              const active = tab === item.key;
              return (
                <button
                  key={item.key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setTab(item.key)}
                  className="h-9 px-4 rounded-full border text-[13.5px] transition-colors"
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
                  {item.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 sm:px-8 pb-10">
        <div
          className="max-w-[660px] mx-auto rounded-2xl border p-2"
          style={{
            backgroundColor: "var(--bg-surface)",
            borderColor: "var(--border-hairline)",
          }}
        >
          {tab === "costs" ? (
            <CostsMetricsPanel costs={costs} metrics={metrics} />
          ) : (
            <ModelSwitcherPanel
              models={models}
              dataPolicy={dataPolicy}
              onUpdateRole={onUpdateRole}
              onUpdateDataPolicy={onUpdateDataPolicy}
              onOpenFullSettings={onOpenFullSettings}
            />
          )}
        </div>
      </div>
    </div>
  );
}
