import {
  Compass,
  LayoutGrid,
  MessageSquareText,
  Moon,
  PanelLeft,
  Settings,
  Sun,
} from "lucide-react";
import type { ResolvedTheme } from "../hooks/useTheme";
import type { RunRecord } from "../types";
import { summarizeRunLabel } from "../utils/foundryMetrics";

export type PrimarySection = "stream" | "workspace" | "controls";

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  activeSection: PrimarySection;
  onSelectSection: (section: PrimarySection) => void;
  runs: RunRecord[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  pendingDecisions: number;
  theme: ResolvedTheme;
  onToggleTheme: () => void;
  onOpenSettings: () => void;
}

export function Sidebar({
  collapsed,
  onToggleCollapse,
  activeSection,
  onSelectSection,
  runs,
  selectedRunId,
  onSelectRun,
  pendingDecisions,
  theme,
  onToggleTheme,
  onOpenSettings,
}: SidebarProps) {
  const navItems: Array<{
    key: PrimarySection;
    label: string;
    icon: typeof MessageSquareText;
    badge?: number;
  }> = [
    { key: "stream", label: "Run stream", icon: MessageSquareText },
    {
      key: "workspace",
      label: "Workspace",
      icon: LayoutGrid,
      badge: pendingDecisions > 0 ? pendingDecisions : undefined,
    },
    { key: "controls", label: "Telemetry", icon: Compass },
  ];

  return (
    <aside
      aria-label="Primary navigation"
      className={`shrink-0 border-r flex flex-col h-full select-none transition-[width] duration-200 ${
        collapsed ? "w-[60px]" : "w-[238px]"
      }`}
      style={{
        backgroundColor: "var(--bg-sidebar)",
        borderColor: "var(--border-hairline)",
      }}
    >
      {/* Top Brand + Collapse Icon */}
      <div
        className={`h-14 flex items-center shrink-0 ${
          collapsed ? "justify-center px-2" : "justify-between px-4"
        }`}
      >
        <button
          type="button"
          onClick={() => {
            if (collapsed) {
              onToggleCollapse();
            } else {
              onSelectSection("stream");
            }
          }}
          className="font-serif font-semibold tracking-tight text-[22px] leading-none text-[var(--ink-primary)]"
          title={collapsed ? "Expand sidebar" : "Foundry Control Plane"}
        >
          {collapsed ? "F" : "Foundry"}
        </button>

        {!collapsed && (
          <button
            type="button"
            onClick={onToggleCollapse}
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
            className="h-8 w-8 rounded-lg flex items-center justify-center text-[var(--ink-secondary)] hover:bg-[var(--bg-hover)] transition-colors"
          >
            <PanelLeft className="w-[17px] h-[17px]" />
          </button>
        )}
      </div>

      {/* Top 3 Nav Links */}
      <nav className={`space-y-1 shrink-0 ${collapsed ? "px-2" : "px-2.5"}`}>
        {navItems.map((item) => {
          const Icon = item.icon;
          const active = activeSection === item.key;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onSelectSection(item.key)}
              title={collapsed ? item.label : undefined}
              aria-current={active ? "page" : undefined}
              className={`w-full h-9 rounded-xl flex items-center transition-colors text-[13.5px] ${
                collapsed ? "justify-center px-0" : "justify-between px-3"
              }`}
              style={{
                backgroundColor: active ? "var(--bg-active)" : "transparent",
                color: active ? "var(--ink-primary)" : "var(--ink-secondary)",
                fontWeight: active ? 500 : 400,
              }}
            >
              <span className="flex items-center gap-2.5 min-w-0">
                <Icon className="w-[16px] h-[16px] shrink-0" />
                {!collapsed && <span className="truncate">{item.label}</span>}
              </span>
              {!collapsed && item.badge !== undefined && (
                <span
                  className="font-mono text-[11px] font-medium tabular"
                  style={{ color: "var(--accent)" }}
                >
                  {item.badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Runs List (hidden when collapsed to icon rail) */}
      <div className="flex-1 min-h-0 overflow-y-auto pt-6 px-2.5">
        {!collapsed && (
          <div className="space-y-1">
            {runs.length === 0 ? (
              <p className="px-3 text-[12px] text-[var(--ink-muted)]">No runs recorded yet</p>
            ) : (
              runs.map((r, idx) => {
                const selected = r.id === selectedRunId && activeSection === "stream";
                const title = summarizeRunLabel(r, idx);
                return (
                  <button
                    key={r.id}
                    type="button"
                    onClick={() => {
                      onSelectRun(r.id);
                      onSelectSection("stream");
                    }}
                    title={`${title} (${r.id})`}
                    className="w-full h-9 px-3 rounded-xl flex items-center justify-between gap-2 text-left text-[13px] transition-colors hover:bg-[var(--bg-hover)]"
                    style={{
                      backgroundColor: selected ? "var(--bg-active)" : undefined,
                      color:
                        r.id === selectedRunId
                          ? "var(--ink-primary)"
                          : "var(--ink-secondary)",
                    }}
                  >
                    <span className="truncate">{title}</span>
                    {(r.phase === "waiting_decision" || r.phase === "building") && (
                      <span
                        className="w-1.5 h-1.5 rounded-full shrink-0"
                        style={{ backgroundColor: "var(--accent)" }}
                      />
                    )}
                  </button>
                );
              })
            )}
          </div>
        )}
      </div>

      {/* Bottom Utility Links (Quick Theme + Centered Settings Modal Trigger) */}
      <div className={`shrink-0 pb-4 pt-2 space-y-1 ${collapsed ? "px-2" : "px-2.5"}`}>
        <button
          type="button"
          onClick={onToggleTheme}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          className={`w-full h-9 rounded-xl flex items-center gap-2.5 text-[13px] text-[var(--ink-secondary)] hover:bg-[var(--bg-hover)] transition-colors ${
            collapsed ? "justify-center px-0" : "px-3"
          }`}
        >
          {theme === "dark" ? (
            <Sun className="w-[16px] h-[16px] shrink-0" />
          ) : (
            <Moon className="w-[16px] h-[16px] shrink-0" />
          )}
          {!collapsed && <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>}
        </button>

        <button
          type="button"
          onClick={onOpenSettings}
          title="Settings (Ctrl+,)"
          aria-label="Open settings"
          className={`w-full h-9 rounded-xl flex items-center gap-2.5 text-[13px] text-[var(--ink-secondary)] hover:bg-[var(--bg-hover)] transition-colors ${
            collapsed ? "justify-center px-0" : "px-3"
          }`}
        >
          <Settings className="w-[16px] h-[16px] shrink-0" />
          {!collapsed && <span>Settings</span>}
        </button>
      </div>
    </aside>
  );
}
