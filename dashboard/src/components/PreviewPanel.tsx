import { useEffect, useState } from "react";
import {
  ExternalLink,
  Monitor,
  RefreshCw,
  Smartphone,
  Tablet,
  Terminal,
} from "lucide-react";
import { api } from "../api";
import type { PreviewUrlResponse } from "../types";

interface PreviewPanelProps {
  runId: string | null;
}

type ViewportSize = "desktop" | "tablet" | "mobile";

const VIEWPORT_WIDTHS: Record<ViewportSize, string> = {
  desktop: "100%",
  tablet: "768px",
  mobile: "390px",
};

export function PreviewPanel({ runId }: PreviewPanelProps) {
  const [env, setEnv] = useState<"local" | "staging" | "production">("local");
  const [viewport, setViewport] = useState<ViewportSize>("desktop");
  const [data, setData] = useState<PreviewUrlResponse | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    if (!runId) {
      setData(null);
      return;
    }
    let active = true;
    api
      .getPreviewUrl(runId, env)
      .then((res) => {
        if (active) setData(res);
      })
      .catch(() => {
        if (active) setData({ run_id: runId, env, url: null, available: false });
      });
    return () => {
      active = false;
    };
  }, [runId, env, reloadTick]);

  return (
    <div className="flex flex-col h-full min-h-0">
      <div
        className="shrink-0 px-4 py-2 border-b flex flex-wrap items-center justify-between gap-2.5"
        style={{
          borderColor: "var(--border-hairline)",
          backgroundColor: "var(--bg-surface)",
        }}
      >
        <div className="flex items-center gap-2">
          <div
            className="inline-flex items-center rounded-[5px] border p-0.5 text-[11px] font-medium"
            style={{
              backgroundColor: "var(--bg-recessed)",
              borderColor: "var(--border-hairline)",
            }}
            role="group"
            aria-label="Preview environment"
          >
            {(["local", "staging", "production"] as const).map((e) => (
              <button
                key={e}
                type="button"
                onClick={() => setEnv(e)}
                className="px-2 py-0.5 rounded-[4px] capitalize transition-colors"
                style={
                  env === e
                    ? { backgroundColor: "var(--bg-surface)", color: "var(--ink-primary)" }
                    : { color: "var(--ink-muted)" }
                }
              >
                {e}
              </button>
            ))}
          </div>

          <div
            className="inline-flex items-center rounded-[5px] border p-0.5"
            style={{
              backgroundColor: "var(--bg-recessed)",
              borderColor: "var(--border-hairline)",
            }}
            role="group"
            aria-label="Preview viewport width"
          >
            {(
              [
                { key: "desktop", icon: Monitor, label: "Desktop viewport" },
                { key: "tablet", icon: Tablet, label: "Tablet viewport (768px)" },
                { key: "mobile", icon: Smartphone, label: "Mobile viewport (390px)" },
              ] as const
            ).map(({ key, icon: Icon, label }) => (
              <button
                key={key}
                type="button"
                onClick={() => setViewport(key)}
                title={label}
                aria-label={label}
                aria-pressed={viewport === key}
                className="p-1 rounded-[4px] transition-colors"
                style={
                  viewport === key
                    ? { backgroundColor: "var(--bg-surface)", color: "var(--ink-primary)" }
                    : { color: "var(--ink-muted)" }
                }
              >
                <Icon className="w-3.5 h-3.5" />
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2 flex-1 max-w-md min-w-[200px]">
          <div
            className="flex-1 h-7 rounded-[5px] border px-2.5 flex items-center justify-between gap-2 font-mono text-[11px] truncate"
            style={{
              backgroundColor: "var(--bg-recessed)",
              borderColor: "var(--border-hairline)",
              color: data?.url ? "var(--ink-secondary)" : "var(--ink-muted)",
            }}
          >
            <span className="truncate">
              {data?.url ?? `No ${env} endpoint configured`}
            </span>
            <span
              className="w-1.5 h-1.5 rounded-full shrink-0"
              style={{
                backgroundColor: data?.available ? "var(--status-nominal)" : "var(--ink-muted)",
              }}
            />
          </div>

          <button
            type="button"
            onClick={() => setReloadTick((t) => t + 1)}
            title="Reload preview"
            aria-label="Reload preview"
            className="h-7 w-7 rounded-[5px] border flex items-center justify-center shrink-0 hover:bg-[var(--bg-hover)]"
            style={{
              backgroundColor: "var(--bg-elevated)",
              borderColor: "var(--border-hairline)",
              color: "var(--ink-secondary)",
            }}
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>

          {data?.available && data.url && (
            <a
              href={data.url}
              target="_blank"
              rel="noreferrer"
              title="Open preview in new tab"
              aria-label="Open preview in new tab"
              className="h-7 w-7 rounded-[5px] border flex items-center justify-center shrink-0 hover:bg-[var(--bg-hover)]"
              style={{
                backgroundColor: "var(--bg-elevated)",
                borderColor: "var(--border-hairline)",
                color: "var(--ink-secondary)",
              }}
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          )}
        </div>
      </div>

      <div
        className="flex-1 min-h-0 overflow-auto p-4 flex justify-center"
        style={{ backgroundColor: "var(--bg-recessed)" }}
      >
        <div
          className="h-full min-h-[340px] rounded-[8px] border overflow-hidden transition-[width] duration-200 flex flex-col"
          style={{
            width: VIEWPORT_WIDTHS[viewport],
            maxWidth: "100%",
            backgroundColor: "var(--bg-surface)",
            borderColor: "var(--border-strong)",
          }}
        >
          {data?.available && data.url ? (
            <iframe
              key={`${data.url}-${reloadTick}`}
              src={data.url}
              title={`Foundry ${env} live preview`}
              className="w-full flex-1 border-0 bg-white"
            />
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
              <div
                className="w-10 h-10 rounded-[8px] border flex items-center justify-center mb-3"
                style={{
                  backgroundColor: "var(--bg-elevated)",
                  borderColor: "var(--border-hairline)",
                  color: "var(--ink-muted)",
                }}
              >
                <Terminal className="w-5 h-5" />
              </div>
              <h3 className="text-[14px] font-semibold text-[var(--ink-primary)]">
                {env === "local"
                  ? "Local compose stack not started yet"
                  : `No ${env} deployment configured`}
              </h3>
              <p className="mt-1.5 text-[12px] text-[var(--ink-secondary)] max-w-sm leading-relaxed">
                {env === "local"
                  ? "Foundry binds the live preview automatically once Wave 1 Docker Compose containers report healthy."
                  : `Promote a verified release candidate to attach a ${env} preview URL.`}
              </p>
              {runId && env === "local" && (
                <code
                  className="mt-4 px-3 py-1.5 rounded-[5px] border font-mono text-[11px]"
                  style={{
                    backgroundColor: "var(--bg-recessed)",
                    borderColor: "var(--border-hairline)",
                    color: "var(--ink-secondary)",
                  }}
                >
                  foundry up
                </code>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
