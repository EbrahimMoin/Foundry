import { Bot } from "lucide-react";
import { CostsMetricsPanel } from "./CostsMetricsPanel";
import type { CostsSummaryResponse, MetricsSummaryResponse } from "../types";

interface TelemetryHubProps {
  costs: CostsSummaryResponse | null;
  metrics: MetricsSummaryResponse | null;
  onOpenModelsDrawer: () => void;
}

export function TelemetryHub({
  costs,
  metrics,
  onOpenModelsDrawer,
}: TelemetryHubProps) {
  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="shrink-0 px-6 sm:px-10 pt-8 pb-4">
        <div className="max-w-[660px] mx-auto flex flex-wrap items-center justify-between gap-3">
          <h1 className="font-serif text-[32px] font-medium tracking-tight text-[var(--ink-primary)]">
            Telemetry
          </h1>
          <button
            type="button"
            onClick={onOpenModelsDrawer}
            className="h-9 px-3.5 rounded-full border inline-flex items-center gap-1.5 text-[12.5px] font-medium hover:bg-[var(--bg-hover)]"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--border-hairline)",
              color: "var(--ink-primary)",
            }}
          >
            <Bot className="w-3.5 h-3.5" style={{ color: "var(--accent)" }} />
            Edit Models in Right Sidebar
          </button>
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
          <CostsMetricsPanel costs={costs} metrics={metrics} />
        </div>
      </div>
    </div>
  );
}
