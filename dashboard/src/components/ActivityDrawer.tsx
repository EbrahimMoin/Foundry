import { useState } from "react";
import { ChevronDown, ChevronUp, Terminal } from "lucide-react";
import type { BlackboardEvent } from "../types";

export function ActivityDrawer({ events }: { events: BlackboardEvent[] }) {
  const [open, setOpen] = useState(true);

  return (
    <section className="border-t border-panelBorder bg-panel">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-800/40"
      >
        <span className="flex items-center gap-2">
          <Terminal className="h-3.5 w-3.5 text-indigo-400" />
          Activity Stream ({events.length} outbox events ordered by monotonic publish_seq)
        </span>
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
      </button>

      {open && (
        <div className="max-h-44 overflow-y-auto border-t border-panelBorder bg-obsidian px-4 py-2 font-mono text-[11px]">
          {events.length === 0 ? (
            <div className="text-slate-500">Waiting for committed Blackboard outbox events...</div>
          ) : (
            [...events].reverse().map((ev) => (
              <div key={`${ev.id}-${ev.publish_seq}`} className="flex items-start gap-3 py-0.5">
                <span className="w-12 shrink-0 text-indigo-400">#{ev.publish_seq ?? "-"}</span>
                <span className="w-40 shrink-0 text-emerald-300">{ev.type}</span>
                <span className="w-32 shrink-0 truncate text-slate-400">
                  {ev.subject_id ?? "-"}
                </span>
                <span className="truncate text-slate-300">{JSON.stringify(ev.payload)}</span>
              </div>
            ))
          )}
        </div>
      )}
    </section>
  );
}
