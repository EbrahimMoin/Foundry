import { useState } from "react";
import type { CostsSummaryResponse, MetricsSummaryResponse } from "../types";
import { computeBudgetMetrics } from "../utils/foundryMetrics";

interface CostsMetricsPanelProps {
  costs: CostsSummaryResponse | null;
  metrics: MetricsSummaryResponse | null;
}

export function CostsMetricsPanel({ costs, metrics }: CostsMetricsPanelProps) {
  const [groupBy, setGroupBy] = useState<"model" | "profile">("model");
  const budget = computeBudgetMetrics(costs);
  const rows = groupBy === "model" ? (costs?.by_model ?? []) : (costs?.by_profile ?? []);

  return (
    <div className="p-4 space-y-6 overflow-y-auto h-full">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="text-[11.5px] text-[var(--ink-muted)]">Run Spend</div>
          <div
            className="mt-1 font-mono text-[22px] font-semibold tabular"
            style={{ color: "var(--ink-primary)" }}
          >
            ${(costs?.spent_usd ?? 0).toFixed(4)}
          </div>
          <div className="mt-1 text-[11.5px] text-[var(--ink-muted)] tabular">
            of ${(costs?.budget_usd ?? 0).toFixed(2)} ({budget.utilizationPct.toFixed(1)}%)
          </div>
        </div>

        <div>
          <div className="text-[11.5px] text-[var(--ink-muted)]">Cache Hit Rate</div>
          <div
            className="mt-1 font-mono text-[22px] font-semibold tabular"
            style={{ color: "var(--status-nominal)" }}
          >
            {((costs?.cache_hit_rate ?? 0) * 100).toFixed(1)}%
          </div>
          <div className="mt-1 text-[11.5px] text-[var(--ink-muted)] tabular">
            ~${budget.estCacheSavingsUsd.toFixed(2)} saved
          </div>
        </div>
      </div>

      <section aria-label="Spend breakdown">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-[11.5px] font-medium text-[var(--ink-muted)]">Breakdown</h2>
          <div className="flex items-center gap-1 text-[11.5px]">
            {(["model", "profile"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setGroupBy(mode)}
                className="px-2 py-0.5 rounded-[5px] capitalize transition-colors"
                style={
                  groupBy === mode
                    ? { backgroundColor: "var(--bg-active)", color: "var(--ink-primary)" }
                    : { color: "var(--ink-muted)" }
                }
              >
                {mode}
              </button>
            ))}
          </div>
        </div>

        <div
          className="rounded-[8px] border divide-y font-mono text-[11.5px] tabular"
          style={{
            backgroundColor: "var(--bg-surface)",
            borderColor: "var(--border-hairline)",
          }}
        >
          {rows.length === 0 ? (
            <div className="p-3 text-[var(--ink-muted)] font-sans">No calls yet.</div>
          ) : (
            rows.map((row) => (
              <div
                key={row.key}
                className="px-3 py-2 flex items-center justify-between gap-2"
                style={{ borderColor: "var(--border-hairline)" }}
              >
                <span className="text-[var(--ink-primary)] truncate">
                  {row.key.replace(/^[^/]+\//, "")}
                </span>
                <div className="flex items-center gap-3 shrink-0">
                  <span className="text-[var(--ink-muted)]">{row.calls}×</span>
                  <span className="font-semibold text-[var(--ink-primary)]">
                    ${row.cost_usd.toFixed(4)}
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      </section>

      {metrics && (
        <section aria-label="Run health" className="space-y-3">
          <h2 className="text-[11.5px] font-medium text-[var(--ink-muted)]">Run Health</h2>
          <div
            className="rounded-[8px] border divide-y text-[12.5px]"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--border-hairline)",
            }}
          >
            {[
              { label: "Active write locks", value: metrics.active_reservations },
              { label: "Locked contracts", value: metrics.contracts_count },
              { label: "G3 dispute rounds", value: metrics.dispute_rounds_total },
              { label: "Outbox events", value: metrics.events_count },
            ].map((item) => (
              <div
                key={item.label}
                className="px-3 py-2 flex items-center justify-between"
                style={{ borderColor: "var(--border-hairline)" }}
              >
                <span className="text-[var(--ink-secondary)]">{item.label}</span>
                <span className="font-mono font-medium tabular text-[var(--ink-primary)]">
                  {item.value}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
