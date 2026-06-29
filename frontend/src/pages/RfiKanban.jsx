import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Clock, MessageSquare, MessagesSquare, Plus } from "lucide-react";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";

const COLUMNS = [
    { id: "draft", label: "Draft", color: "border-ink-line", accent: "text-ink-muted" },
    { id: "sent", label: "Sent", color: "border-gold", accent: "text-gold" },
    { id: "responded", label: "Responded", color: "border-navy", accent: "text-navy" },
    { id: "closed", label: "Closed", color: "border-success", accent: "text-success" },
];

const PRIORITY_CLS = {
    critical: "border-l-destructive",
    urgent: "border-l-warning",
    standard: "border-l-navy",
};

export default function RfiKanban() {
    const [rfis, setRfis] = useState([]);
    const [loading, setLoading] = useState(true);
    const [dragId, setDragId] = useState(null);

    const load = async () => {
        try {
            const { data } = await api.get("/api/rfis");
            setRfis(data.items);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setLoading(false);
    };

    useEffect(() => {
        load();
    }, []);

    const grouped = useMemo(() => {
        const g = { draft: [], sent: [], responded: [], closed: [] };
        for (const r of rfis) (g[r.status] ||= []).push(r);
        return g;
    }, [rfis]);

    const moveTo = async (id, status) => {
        try {
            await api.put(`/api/rfis/${id}`, { status });
            load();
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    return (
        <div className="min-h-full bg-background">
            <div className="border-b border-ink-line bg-white px-10 py-10">
                <div className="flex items-end justify-between">
                    <div>
                        <div className="text-overline">RFI Tracker</div>
                        <h1 className="mt-2 font-heading text-5xl font-black leading-none tracking-tight text-navy">
                            Kanban board
                        </h1>
                        <p className="mt-2 text-sm text-ink-muted">
                            Drag cards between columns to advance status.
                        </p>
                    </div>
                    <div className="font-mono text-xs uppercase tracking-wider text-ink-muted">
                        {rfis.length} RFIs total
                    </div>
                </div>
            </div>

            <div className="container-steel py-10">
                {loading ? (
                    <div className="grid gap-4 md:grid-cols-4">
                        {Array.from({ length: 4 }).map((_, i) => (
                            <Skeleton key={i} className="h-96 rounded-none" />
                        ))}
                    </div>
                ) : (
                    <div className="grid gap-4 md:grid-cols-4">
                        {COLUMNS.map((col) => (
                            <div
                                key={col.id}
                                data-testid={`kanban-column-${col.id}`}
                                onDragOver={(e) => e.preventDefault()}
                                onDrop={() => {
                                    if (dragId) moveTo(dragId, col.id);
                                    setDragId(null);
                                }}
                                className="card-steel flex min-h-[420px] flex-col"
                            >
                                <div className={`flex items-center justify-between border-b-2 ${col.color} bg-white px-4 py-3`}>
                                    <div className={`font-heading text-sm font-bold uppercase tracking-wide ${col.accent}`}>
                                        {col.label}
                                    </div>
                                    <div className="font-mono text-[11px] text-ink-muted">
                                        {grouped[col.id].length}
                                    </div>
                                </div>
                                <div className="flex-1 space-y-2 p-3">
                                    {grouped[col.id].length === 0 && (
                                        <div className="p-6 text-center font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                            Drop RFIs here
                                        </div>
                                    )}
                                    {grouped[col.id].map((r) => (
                                        <div
                                            key={r.id}
                                            data-testid={`rfi-card-${r.id}`}
                                            draggable
                                            onDragStart={() => setDragId(r.id)}
                                            className={`cursor-grab border border-ink-line bg-white p-3 ${PRIORITY_CLS[r.priority]} border-l-4 transition-all hover:-translate-y-0.5 hover:shadow-md active:cursor-grabbing`}
                                        >
                                            <div className="flex items-center justify-between">
                                                <div className="font-mono text-[10px] uppercase tracking-wider text-gold">
                                                    {r.rfi_number}
                                                </div>
                                                {r.blocking && (
                                                    <span className="inline-flex items-center gap-1 border border-destructive px-1 py-0.5 font-mono text-[9px] uppercase text-destructive">
                                                        <AlertTriangle size={8} />
                                                        Block
                                                    </span>
                                                )}
                                            </div>
                                            <div className="mt-2 font-heading text-sm font-semibold uppercase leading-tight tracking-tight text-navy line-clamp-2">
                                                {r.subject}
                                            </div>
                                            <div className="mt-1 line-clamp-2 text-xs text-ink-muted">
                                                {r.body}
                                            </div>
                                            <div className="mt-3 flex items-center justify-between border-t border-ink-line pt-2 font-mono text-[9px] uppercase tracking-wider text-ink-muted">
                                                <span className="inline-flex items-center gap-1">
                                                    <Clock size={10} />
                                                    {new Date(r.created_at).toLocaleDateString()}
                                                </span>
                                                <span>{r.priority}</span>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
