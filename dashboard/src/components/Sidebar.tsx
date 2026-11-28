import { useMemo, useState } from "react";
import {
  Compass,
  Layers,
  LayoutGrid,
  Moon,
  PanelLeft,
  Plus,
  Search,
  Settings,
  Sun,
  Trash2,
} from "lucide-react";
import type { ResolvedTheme } from "../hooks/useTheme";
import type { ChatSessionSummary } from "../types";

export type PrimarySection = "stream" | "workspace" | "agents" | "controls";

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  activeSection: PrimarySection;
  onSelectSection: (section: PrimarySection) => void;
  chats: ChatSessionSummary[];
  selectedRunId: string | null;
  onNewChat: () => void;
  onSelectChat: (runId: string) => void;
  onDeleteChat: (runId: string) => Promise<void>;
  onPurgeTestRuns: () => Promise<void>;
  pendingDecisions: number;
  enabledAgentCount: number;
  theme: ResolvedTheme;
  onToggleTheme: () => void;
  onOpenSettings: () => void;
}

export function Sidebar({
  collapsed,
  onToggleCollapse,
  activeSection,
  onSelectSection,
  chats,
  selectedRunId,
  onNewChat,
  onSelectChat,
  onDeleteChat,
  onPurgeTestRuns,
  pendingDecisions,
  enabledAgentCount,
  theme,
  onToggleTheme,
  onOpenSettings,
}: SidebarProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [showAllRuns, setShowAllRuns] = useState(false);
  const [purging, setPurging] = useState(false);

  const navItems: Array<{
    key: PrimarySection;
    label: string;
    icon: typeof LayoutGrid;
    badge?: number;
  }> = [
    {
      key: "workspace",
      label: "Workspace",
      icon: LayoutGrid,
      badge: pendingDecisions > 0 ? pendingDecisions : undefined,
    },
    {
      key: "agents",
      label: "Agents",
      icon: Layers,
      badge: enabledAgentCount > 0 ? enabledAgentCount : undefined,
    },
    { key: "controls", label: "Telemetry", icon: Compass },
  ];

  const { visibleChats, nonChatCount } = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const chatFirst = chats.filter((c) => c.is_chat_session);
    const nonChat = chats.length - chatFirst.length;

    const pool = showAllRuns ? chats : chatFirst;
    const filtered = q
      ? pool.filter(
          (c) =>
            c.title.toLowerCase().includes(q) ||
            c.project_id.toLowerCase().includes(q) ||
            c.run_id.toLowerCase().includes(q)
        )
      : pool;
    return {
      visibleChats: filtered,
      nonChatCount: nonChat,
    };
  }, [chats, searchQuery, showAllRuns]);

  return (
    <aside
      aria-label="Primary navigation"
      className={`shrink-0 border-r flex flex-col h-full select-none transition-[width] duration-200 ${
        collapsed ? "w-[60px]" : "w-[244px]"
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
              onNewChat();
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

      {/* New Chat Button + Primary Nav Links */}
      <div className={`space-y-1.5 shrink-0 ${collapsed ? "px-2" : "px-2.5"}`}>
        <button
          type="button"
          onClick={onNewChat}
          title="New chat"
          aria-label="Start new chat"
          className={`w-full h-9 rounded-xl border flex items-center transition-colors text-[13.5px] font-medium hover:bg-[var(--bg-hover)] ${
            collapsed ? "justify-center px-0" : "gap-2 px-3"
          }`}
          style={{
            backgroundColor:
              activeSection === "stream" && selectedRunId === null
                ? "var(--bg-active)"
                : "var(--bg-surface)",
            borderColor: "var(--border-hairline)",
            color: "var(--ink-primary)",
          }}
        >
          <Plus className="w-4 h-4 shrink-0" style={{ color: "var(--accent)" }} />
          {!collapsed && <span>New chat</span>}
        </button>

        <nav className="space-y-1 pt-1" aria-label="Workspace tools">
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
      </div>

      {/* Chat History ("Recents") */}
      <div className="flex-1 min-h-0 flex flex-col pt-5 px-2.5">
        {!collapsed && (
          <>
            <div className="px-2 pb-2 flex items-center justify-between gap-2 shrink-0">
              <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--ink-muted)]">
                Recents
              </span>
              {chats.length > 4 && (
                <div className="relative flex-1 max-w-[128px]">
                  <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-[var(--ink-muted)] pointer-events-none" />
                  <input
                    type="search"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Filter…"
                    aria-label="Filter recent chats"
                    className="w-full h-6 pl-6 pr-2 rounded-md border text-[11px] placeholder:text-[var(--ink-muted)]"
                    style={{
                      backgroundColor: "var(--bg-canvas)",
                      borderColor: "var(--border-hairline)",
                      color: "var(--ink-primary)",
                    }}
                  />
                </div>
              )}
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto space-y-0.5 pr-0.5">
              {visibleChats.length === 0 ? (
                <p className="px-2.5 py-2 text-[12px] text-[var(--ink-muted)]">
                  No chats yet.
                </p>
              ) : (
                visibleChats.map((chat) => {
                  const selected = chat.run_id === selectedRunId && activeSection === "stream";
                  return (
                    <div
                      key={chat.run_id}
                      className="group relative flex items-center rounded-lg hover:bg-[var(--bg-hover)]"
                      style={{
                        backgroundColor: selected ? "var(--bg-active)" : undefined,
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => onSelectChat(chat.run_id)}
                        title={`${chat.title} (${chat.project_id})`}
                        className="flex-1 min-w-0 h-8 pl-2.5 pr-7 flex items-center justify-between gap-2 text-left text-[12.5px] transition-colors"
                        style={{
                          color:
                            chat.run_id === selectedRunId
                              ? "var(--ink-primary)"
                              : "var(--ink-secondary)",
                          fontWeight: selected ? 500 : 400,
                        }}
                      >
                        <span className="truncate">{chat.title}</span>
                        {(chat.phase === "waiting_decision" || chat.phase === "building") && (
                          <span
                            className="w-1.5 h-1.5 rounded-full shrink-0"
                            style={{ backgroundColor: "var(--accent)" }}
                          />
                        )}
                      </button>

                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          void onDeleteChat(chat.run_id);
                        }}
                        title={`Delete ${chat.title}`}
                        aria-label={`Delete ${chat.title}`}
                        className="absolute right-1 h-6 w-6 rounded-md flex items-center justify-center opacity-0 group-hover:opacity-100 focus:opacity-100 text-[var(--ink-muted)] hover:text-[var(--status-danger)] transition-opacity"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  );
                })
              )}

              {nonChatCount > 0 && (
                <div className="pt-2 mt-1 border-t space-y-1" style={{ borderColor: "var(--border-hairline)" }}>
                  <button
                    type="button"
                    disabled={purging}
                    onClick={async () => {
                      setPurging(true);
                      try {
                        await onPurgeTestRuns();
                      } finally {
                        setPurging(false);
                      }
                    }}
                    className="w-full text-left px-2.5 py-1 text-[11px] font-medium hover:underline disabled:opacity-50"
                    style={{ color: "var(--status-warn)" }}
                  >
                    {purging
                      ? "Clearing test runs…"
                      : `Clear ${nonChatCount} legacy/test ${nonChatCount === 1 ? "run" : "runs"}`}
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowAllRuns((prev) => !prev)}
                    className="w-full text-left px-2.5 py-1 text-[11px] text-[var(--ink-muted)] hover:text-[var(--ink-primary)]"
                  >
                    {showAllRuns ? "Hide non-chat runs" : `Inspect all runs (${chats.length})`}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* Bottom Utility Links (Theme + Settings Modal) */}
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
