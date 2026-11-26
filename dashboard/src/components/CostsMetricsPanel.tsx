import type { CostsSummaryResponse, MetricsSummaryResponse } from "../types";

interface CostsMetricsPanelProps {
  costs: CostsSummaryResponse | null;
  metrics: MetricsSummaryResponse | null;
}

export function CostsMetricsPanel({ costs, metrics }: CostsMetricsPanelProps) {
  if (!costs && !metrics) {
    return <p className="text-xs text-slate-500">Select a run to inspect costs and telemetry.</p>;
  }

  return (
    <div className="space-y-3 text-xs">
      {costs && (
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded border border-panelBorder bg-obsidian p-2.5">
            <div className="text-[11px] text-slate-400">Run Spend</div>
            <div className="mt-0.5 font-mono text-sm font-semibold text-amber-300">
              ${costs.spent_usd.toFixed(4)}
            </div>
          </div>
          <div className="rounded border border-panelBorder bg-obsidian p-2.5">
            <div className="text-[11px] text-slate-400">Prefix Cache Hit</div>
            <div className="mt-0.5 font-mono text-sm font-semibold text-emerald-300">
              {(costs.cache_hit_rate * 100).toFixed(1)}%
            </div>
          </div>
        </div>
      )}

      {costs && costs.by_model.length > 0 && (
        <div className="rounded border border-panelBorder bg-obsidian p-2">
          <div className="mb-1.5 text-[11px] font-semibold text-slate-400">By Model</div>
          {costs.by_model.map((m) => (
            <div key={m.key} className="flex items-center justify-between py-0.5 font-mono text-[11px]">
              <span className="truncate text-slate-200">{m.key}</span>
              <span className="text-amber-300">
                {m.calls}x · ${m.cost_usd.toFixed(4)}
              </span>
            </div>
          ))}
        </div>
      )}

      {metrics && (
        <div className="rounded border border-panelBorder bg-obsidian p-2 space-y-1">
          <div className="text-[11px] font-semibold text-slate-400">Run Health</div>
          <div className="flex justify-between text-slate-300">
            <span>Active Write Locks</span>
            <span className="font-mono">{metrics.active_reservations}</span>
          </div>
          <div className="flex justify-between text-slate-300">
            <span>Contracts Locked</span>
            <span className="font-mono">{metrics.contracts_count}</span>
          </div>
          <div className="flex justify-between text-slate-300">
            <span>G3 Dispute Rounds</span>
            <span className="font-mono">{metrics.dispute_rounds_total}</span>
          </div>
          <div className="flex justify-between text-slate-300">
            <span>Outbox Events</span>
            <span className="font-mono">{metrics.events_count}</span>
          </div>
        </div>
      )}
    </div>
  );
}
