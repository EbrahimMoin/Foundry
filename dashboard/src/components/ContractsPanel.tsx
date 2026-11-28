import { useEffect, useRef, useState } from "react";
import { Copy, Lock, X } from "lucide-react";
import { api } from "../api";
import type { ContractRecord, ContractVersionsResponse } from "../types";

interface ContractsPanelProps {
  contracts: ContractRecord[];
  onAnnounce?: (msg: string) => void;
}

export function ContractsPanel({ contracts, onAnnounce }: ContractsPanelProps) {
  const [selected, setSelected] = useState<ContractRecord | null>(null);
  const [versionsData, setVersionsData] = useState<ContractVersionsResponse | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const dialogRef = useRef<HTMLDialogElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (selected) {
      if (!dialog.open) dialog.showModal();
    } else if (dialog.open) {
      dialog.close();
    }
  }, [selected]);

  // Mandatory Safari light-dismiss fallback for browsers lacking <dialog closedby>
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if ("closedBy" in HTMLDialogElement.prototype) return;

    const handleBackdropClick = (event: MouseEvent) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      const inside =
        rect.top <= event.clientY &&
        event.clientY <= rect.top + rect.height &&
        rect.left <= event.clientX &&
        event.clientX <= rect.left + rect.width;
      if (!inside) dialog.close();
    };
    dialog.addEventListener("click", handleBackdropClick);
    return () => dialog.removeEventListener("click", handleBackdropClick);
  }, []);

  useEffect(() => {
    if (!selected) {
      setVersionsData(null);
      setSelectedVersion(null);
      return;
    }
    let active = true;
    setLoading(true);
    api
      .getContractVersions(selected.id)
      .then((res) => {
        if (!active) return;
        setVersionsData(res);
        const latest =
          res.versions.find((v) => v.version === res.current_version) ??
          res.versions[res.versions.length - 1] ??
          null;
        setSelectedVersion(latest?.version ?? null);
      })
      .catch(() => {
        if (active) setVersionsData(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selected]);

  const activeVer =
    versionsData?.versions.find((v) => v.version === selectedVersion) ??
    versionsData?.versions[versionsData.versions.length - 1] ??
    null;

  const formattedBody = activeVer ? JSON.stringify(activeVer.body, null, 2) : null;

  return (
    <div className="p-5 h-full overflow-y-auto max-w-4xl">
      {contracts.length === 0 ? (
        <div className="py-12 text-center text-[13px] text-[var(--ink-muted)]">
          No contracts locked yet.
        </div>
      ) : (
        <div
          className="rounded-[8px] border divide-y"
          style={{
            backgroundColor: "var(--bg-surface)",
            borderColor: "var(--border-hairline)",
          }}
        >
          {contracts.map((c) => {
            const isFrozen = c.status === "frozen";
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => setSelected(c)}
                className="w-full text-left px-4 py-3.5 flex items-center justify-between gap-4 transition-colors hover:bg-[var(--bg-hover)]"
                style={{ borderColor: "var(--border-hairline)" }}
              >
                <div className="min-w-0">
                  <div className="text-[13.5px] font-medium text-[var(--ink-primary)] truncate">
                    {c.name}
                  </div>
                  <div className="mt-0.5 text-[11.5px] text-[var(--ink-muted)]">
                    {c.kind} · {c.domain}
                    {c.breaking_in_wave > 0 && (
                      <span style={{ color: "var(--status-danger)" }}>
                        {" "}
                        · {c.breaking_in_wave} breaking
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-3 shrink-0 font-mono text-[11.5px]">
                  <span className="text-[var(--ink-secondary)]">v{c.current_version}</span>
                  <span
                    className="inline-flex items-center gap-1"
                    style={{
                      color: isFrozen ? "var(--status-nominal)" : "var(--status-active)",
                    }}
                  >
                    <Lock className="w-3 h-3" />
                    {c.status}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      )}

      <dialog
        ref={dialogRef}
        closedby="any"
        className="foundry-dialog"
        aria-labelledby="contract-viewer-title"
        onClose={() => setSelected(null)}
      >
        {selected && (
          <div className="flex flex-col max-h-[calc(100vh-3rem)]">
            <div
              className="px-6 py-4 border-b flex items-center justify-between gap-4 shrink-0"
              style={{ borderColor: "var(--border-hairline)" }}
            >
              <div>
                <h2
                  id="contract-viewer-title"
                  className="text-[15.5px] font-semibold text-[var(--ink-primary)]"
                >
                  {selected.name}
                </h2>
                <div className="text-[12px] text-[var(--ink-muted)] mt-0.5">
                  {selected.kind} · {selected.domain} · v{selected.current_version} ({selected.status})
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                {formattedBody && (
                  <button
                    type="button"
                    onClick={() => {
                      navigator.clipboard?.writeText(formattedBody).catch(() => {});
                      onAnnounce?.("Copied contract spec");
                    }}
                    className="h-8 px-2.5 rounded-lg inline-flex items-center gap-1.5 text-[12px] text-[var(--ink-secondary)] hover:bg-[var(--bg-hover)]"
                  >
                    <Copy className="w-3.5 h-3.5" />
                    Copy
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setSelected(null)}
                  aria-label="Close contract viewer"
                  className="h-8 w-8 rounded-full flex items-center justify-center hover:bg-[var(--bg-hover)] text-[var(--ink-muted)]"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            <div className="p-6 overflow-y-auto space-y-4">
              {loading ? (
                <p className="text-[13px] text-[var(--ink-muted)]">
                  Loading immutable contract versions…
                </p>
              ) : activeVer ? (
                <>
                  {versionsData && versionsData.versions.length > 1 && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      {versionsData.versions.map((ver) => {
                        const active = ver.version === activeVer.version;
                        return (
                          <button
                            key={ver.version}
                            type="button"
                            onClick={() => setSelectedVersion(ver.version)}
                            className="px-2.5 py-1 rounded-full border font-mono text-[11px]"
                            style={
                              active
                                ? {
                                    backgroundColor: "var(--bg-active)",
                                    borderColor: "var(--border-strong)",
                                    color: "var(--ink-primary)",
                                  }
                                : {
                                    backgroundColor: "transparent",
                                    borderColor: "var(--border-hairline)",
                                    color: "var(--ink-muted)",
                                  }
                            }
                          >
                            v{ver.version} · {ver.change_kind}
                          </button>
                        );
                      })}
                    </div>
                  )}

                  {activeVer.change_summary && (
                    <p className="text-[12.5px] text-[var(--ink-secondary)]">
                      {activeVer.change_summary}
                    </p>
                  )}

                  {activeVer.pinned_pointers.length > 0 && (
                    <div className="text-[11.5px] font-mono text-[var(--ink-muted)]">
                      Pinned pointers: {activeVer.pinned_pointers.join(", ")}
                    </div>
                  )}

                  <pre
                    className="rounded-[8px] border p-4 font-mono text-[12px] leading-relaxed overflow-x-auto text-[var(--ink-primary)]"
                    style={{
                      backgroundColor: "var(--bg-recessed)",
                      borderColor: "var(--border-hairline)",
                    }}
                  >
                    <code>{formattedBody}</code>
                  </pre>
                </>
              ) : (
                <p className="text-[13px] text-[var(--ink-secondary)]">
                  No immutable version snapshots recorded yet for {selected.id}.
                </p>
              )}
            </div>
          </div>
        )}
      </dialog>
    </div>
  );
}
