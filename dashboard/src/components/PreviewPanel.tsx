import { useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import type { PreviewUrlResponse } from "../types";

interface PreviewPanelProps {
  preview: PreviewUrlResponse | null;
  env: "local" | "staging" | "production";
  onChangeEnv: (env: "local" | "staging" | "production") => void;
}

export function PreviewPanel({ preview, env, onChangeEnv }: PreviewPanelProps) {
  const [reloadNonce, setReloadNonce] = useState(0);

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center justify-between gap-2 rounded border border-panelBorder bg-obsidian px-3 py-1.5 text-xs">
        <div className="flex items-center gap-1.5">
          {(["local", "staging", "production"] as const).map((item) => (
            <button
              key={item}
              onClick={() => onChangeEnv(item)}
              className={`rounded px-2 py-0.5 capitalize ${
                env === item ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {item}
            </button>
          ))}
        </div>
        <div className="flex flex-1 items-center gap-2 overflow-hidden">
          <span className="truncate font-mono text-slate-400">
            {preview?.url ?? `No ${env} preview registered`}
          </span>
          {preview?.url && (
            <>
              <button
                onClick={() => setReloadNonce((n) => n + 1)}
                title="Reload preview"
                className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-white"
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
              <a
                href={preview.url}
                target="_blank"
                rel="noreferrer"
                title="Open in browser tab"
                className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-white"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </>
          )}
        </div>
      </div>

      {preview?.available && preview.url ? (
        <iframe
          key={`${preview.url}-${reloadNonce}`}
          src={preview.url}
          title="Foundry Application Preview"
          className="min-h-[340px] w-full flex-1 rounded border border-panelBorder bg-white"
        />
      ) : (
        <div className="flex min-h-[280px] flex-1 items-center justify-center rounded border border-dashed border-slate-800 text-xs text-slate-500">
          Preview URL unavailable for `{env}`. Deploy via LocalComposeTarget (P6) or local dev server.
        </div>
      )}
    </div>
  );
}
