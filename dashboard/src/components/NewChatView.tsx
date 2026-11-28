import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowUp, Layers, Sparkles } from "lucide-react";
import type { AgentRosterConfig, AutonomyConfig } from "../types";

interface NewChatViewProps {
  defaultProjectName: string;
  autonomy: AutonomyConfig | null;
  roster: AgentRosterConfig | null;
  onAutonomyChange: (preset: AutonomyConfig["preset"]) => Promise<void>;
  onOpenAgentsStudio: () => void;
  onStartChat: (payload: {
    prompt: string;
    project_id?: string;
    autonomy_preset?: AutonomyConfig["preset"];
  }) => Promise<void>;
}

const AUTONOMY_PRESETS: AutonomyConfig["preset"][] = [
  "autopilot",
  "balanced",
  "architect",
  "full_control",
  "benchmark",
];

const STARTER_PROMPTS = [
  {
    label: "Bookmark workspace",
    prompt:
      "Build a full-stack bookmark manager with cursor pagination, faceted tags, and PostgreSQL full-text search.",
  },
  {
    label: "Multi-tenant SaaS starter",
    prompt:
      "Scaffold an organization-based SaaS app with role-based permissions, audit log, and API key management.",
  },
  {
    label: "Review & test workflow",
    prompt:
      "Create an approval workflow service with OpenAPI contracts, Wave 1 migrations, and black-box Playwright tests.",
  },
];

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Morning — what are we building?";
  if (hour < 17) return "Afternoon — what are we building?";
  return "Evening — what are we building?";
}

export function NewChatView({
  defaultProjectName,
  autonomy,
  roster,
  onAutonomyChange,
  onOpenAgentsStudio,
  onStartChat,
}: NewChatViewProps) {
  const [prompt, setPrompt] = useState("");
  const [projectName, setProjectName] = useState(defaultProjectName || "app");
  const [projectDirty, setProjectDirty] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!projectDirty && defaultProjectName) {
      setProjectName(defaultProjectName);
    }
  }, [defaultProjectName, projectDirty]);

  const enabledAgents = (roster?.agents ?? []).filter((a) => a.enabled);
  const l1Count = enabledAgents.filter((a) => a.layer === 1).length;
  const l2Count = enabledAgents.filter((a) => a.layer === 2).length;
  const l3Count = enabledAgents.filter((a) => a.layer === 3).length;

  const submitPrompt = async () => {
    const cleaned = prompt.trim();
    if (!cleaned || submitting) return;
    setSubmitting(true);
    try {
      await onStartChat({
        prompt: cleaned,
        project_id: projectName.trim() || defaultProjectName || "app",
        autonomy_preset: autonomy?.preset,
      });
      setPrompt("");
    } finally {
      setSubmitting(false);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submitPrompt();
    }
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-6 sm:px-10 py-10 flex flex-col items-center justify-center">
      <div className="w-full max-w-[660px] mx-auto space-y-6">
        {/* Centered Editorial Greeting */}
        <div className="text-center space-y-2">
          <div className="inline-flex items-center justify-center gap-2.5">
            <span
              className="w-7 h-7 rounded-full inline-flex items-center justify-center"
              style={{
                backgroundColor: "var(--accent-soft)",
                color: "var(--accent)",
              }}
            >
              <Sparkles className="w-4 h-4" />
            </span>
            <h1 className="font-serif text-[30px] sm:text-[34px] font-medium tracking-tight text-[var(--ink-primary)]">
              {getGreeting()}
            </h1>
          </div>
          <p className="text-[13.5px] text-[var(--ink-muted)]">
            Start a clean contract-first run backed by your 3-layer agent hierarchy.
          </p>
        </div>

        {/* Centered Rounded Botanical Composer */}
        <div
          className="rounded-2xl border p-4 space-y-3 transition-colors focus-within:border-[var(--border-strong)]"
          style={{
            backgroundColor: "var(--bg-surface)",
            borderColor: "var(--border-hairline)",
            boxShadow: "0 10px 32px -16px rgba(20, 16, 10, 0.14)",
          }}
        >
          <textarea
            ref={textareaRef}
            rows={3}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={submitting}
            placeholder="Describe the app, feature, or architecture you want Foundry to build…"
            aria-label="New Foundry run prompt"
            className="w-full resize-none bg-transparent font-serif text-[18px] leading-[1.55] outline-none placeholder:text-[var(--ink-muted)]"
            style={{ color: "var(--ink-primary)" }}
          />

          <div
            className="pt-2 border-t flex flex-wrap items-center justify-between gap-2.5"
            style={{ borderColor: "var(--border-hairline)" }}
          >
            <div className="flex flex-wrap items-center gap-2">
              <label className="inline-flex items-center gap-1.5 text-[12px] text-[var(--ink-muted)]">
                <span>Project</span>
                <input
                  type="text"
                  value={projectName}
                  onChange={(e) => {
                    setProjectDirty(true);
                    setProjectName(e.target.value);
                  }}
                  aria-label="Project slug"
                  className="h-7 w-28 rounded-lg border px-2 font-mono text-[11.5px]"
                  style={{
                    backgroundColor: "var(--bg-canvas)",
                    borderColor: "var(--border-hairline)",
                    color: "var(--ink-primary)",
                  }}
                />
              </label>

              {autonomy && (
                <select
                  aria-label="Autonomy preset"
                  value={autonomy.preset}
                  onChange={(e) =>
                    void onAutonomyChange(e.target.value as AutonomyConfig["preset"])
                  }
                  className="h-7 rounded-lg border px-2 text-[12px]"
                  style={{
                    backgroundColor: "var(--bg-canvas)",
                    borderColor: "var(--border-hairline)",
                    color: "var(--ink-secondary)",
                  }}
                >
                  {AUTONOMY_PRESETS.map((p) => (
                    <option key={p} value={p} style={{ backgroundColor: "var(--bg-surface)" }}>
                      {p}
                    </option>
                  ))}
                </select>
              )}

              <button
                type="button"
                onClick={onOpenAgentsStudio}
                title="Configure Layer 1, 2 & 3 agents and API keys"
                className="h-7 px-2.5 rounded-lg border inline-flex items-center gap-1.5 text-[12px] hover:bg-[var(--bg-hover)] transition-colors"
                style={{
                  backgroundColor: "var(--bg-canvas)",
                  borderColor: "var(--border-hairline)",
                  color: "var(--ink-secondary)",
                }}
              >
                <Layers className="w-3.5 h-3.5" style={{ color: "var(--accent)" }} />
                <span className="font-mono text-[11px] tabular">
                  L1:{l1Count} · L2:{l2Count} · L3:{l3Count}
                </span>
              </button>
            </div>

            <button
              type="button"
              disabled={!prompt.trim() || submitting}
              onClick={() => void submitPrompt()}
              aria-label="Start new run"
              className="w-9 h-9 rounded-full inline-flex items-center justify-center transition-opacity disabled:opacity-45"
              style={{
                backgroundColor: prompt.trim() ? "var(--accent)" : "var(--accent-pill)",
                color: "#ffffff",
              }}
            >
              <ArrowUp className="w-[18px] h-[18px]" />
            </button>
          </div>
        </div>

        {/* Quiet Starter Prompt Pills */}
        <div className="flex flex-wrap items-center justify-center gap-2">
          {STARTER_PROMPTS.map((item) => (
            <button
              key={item.label}
              type="button"
              onClick={() => {
                setPrompt(item.prompt);
                textareaRef.current?.focus();
              }}
              className="h-8 px-3.5 rounded-full border text-[12px] text-[var(--ink-secondary)] hover:text-[var(--ink-primary)] hover:bg-[var(--bg-hover)] transition-colors"
              style={{
                backgroundColor: "var(--bg-surface)",
                borderColor: "var(--border-hairline)",
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
