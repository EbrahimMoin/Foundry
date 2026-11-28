import { Copy } from "lucide-react";
import type { AgentRecord, ModelsConfig } from "../types";
import {
  groupAgentsByHierarchy,
  normalizeProfileKey,
  resolveProfileFamily,
  type AgentTier,
} from "../utils/foundryMetrics";

interface AgentsPanelProps {
  agents: AgentRecord[];
  models: ModelsConfig | null;
  onAnnounce?: (msg: string) => void;
}

const TIER_TITLES: Array<{ key: AgentTier; title: string }> = [
  { key: "orchestrator", title: "Orchestrator" },
  { key: "leads", title: "Leads" },
  { key: "implementers", title: "Implementers" },
  { key: "verifiers", title: "G3 / G4 Verifiers" },
];

function statusColor(status: string): string {
  switch (status) {
    case "running":
      return "var(--status-nominal)";
    case "waiting_decision":
      return "var(--status-warn)";
    case "stopped":
    case "failed":
      return "var(--status-danger)";
    case "completed":
      return "var(--status-active)";
    default:
      return "var(--ink-muted)";
  }
}

export function AgentsPanel({ agents, models, onAnnounce }: AgentsPanelProps) {
  const grouped = groupAgentsByHierarchy(agents);

  if (agents.length === 0) {
    return (
      <div className="p-8 text-center text-[13px] text-[var(--ink-muted)]">
        No agents registered for this run.
      </div>
    );
  }

  return (
    <div className="p-5 h-full overflow-y-auto space-y-6 max-w-4xl">
      {TIER_TITLES.map((tier) => {
        const items = grouped[tier.key];
        if (items.length === 0) return null;
        return (
          <section key={tier.key} aria-label={tier.title}>
            <h3 className="text-[11.5px] font-medium text-[var(--ink-muted)] mb-2">
              {tier.title}
            </h3>
            <div
              className="rounded-[8px] border divide-y"
              style={{
                backgroundColor: "var(--bg-surface)",
                borderColor: "var(--border-hairline)",
              }}
            >
              {items.map((agent) => {
                const normProfile = normalizeProfileKey(agent.model_profile);
                const family = models
                  ? resolveProfileFamily(models, normProfile)
                  : "unknown";
                const color = statusColor(agent.status);
                const isRunning = agent.status === "running";
                return (
                  <div
                    key={agent.id}
                    className="px-4 py-3 flex items-center justify-between gap-4"
                    style={{ borderColor: "var(--border-hairline)" }}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <span
                        className={`w-2 h-2 rounded-full shrink-0 ${isRunning ? "animate-pulse" : ""}`}
                        style={{ backgroundColor: color }}
                      />
                      <div className="min-w-0">
                        <div className="text-[13px] font-medium text-[var(--ink-primary)] truncate">
                          {agent.role}
                          {agent.domain && (
                            <span className="text-[var(--ink-muted)] font-normal">
                              {" "}
                              · {agent.domain}
                            </span>
                          )}
                        </div>
                        <div className="font-mono text-[11px] text-[var(--ink-muted)]">
                          {normProfile}
                          {family !== "unknown" ? ` (${family})` : ""}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-[11.5px] font-medium" style={{ color }}>
                        {agent.status.replace(/_/g, " ")}
                      </span>
                      {agent.graph_thread_id && (
                        <button
                          type="button"
                          onClick={() => {
                            navigator.clipboard?.writeText(agent.graph_thread_id!).catch(() => {});
                            onAnnounce?.("Copied thread ID");
                          }}
                          title={`Copy thread ID: ${agent.graph_thread_id}`}
                          aria-label={`Copy thread ID for ${agent.role}`}
                          className="h-7 w-7 rounded-[6px] flex items-center justify-center text-[var(--ink-muted)] hover:text-[var(--ink-primary)] hover:bg-[var(--bg-hover)]"
                        >
                          <Copy className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
