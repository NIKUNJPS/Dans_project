import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Download, FileOutput, History, Search, Trash2 } from "lucide-react";
import { api, errMessage } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { BlockchainBadge } from "@/components/brand/BlockchainBadge";
import { tokenStore } from "@/lib/auth";

const EXPORTS = [
    { fmt: "docx", label: "DOCX" },
    { fmt: "pdf", label: "PDF" },
    { fmt: "xlsx", label: "XLSX" },
    { fmt: "csv", label: "CSV" },
    { fmt: "markdown", label: "MD" },
];

const TABS = [
    { id: "all", label: "All reports" },
    { id: "project", label: "Project reports" },
    { id: "quick", label: "Quick analyses" },
];

function StatusPill({ a }) {
    const tone =
        a.status === "complete"
            ? "border-success text-success"
            : a.status === "failed"
              ? "border-destructive text-destructive"
              : "border-gold text-navy";
    const running = a.status === "processing" || a.status === "queued";
    const label =
        running && a.progress?.total > 1 ? `${a.status} · ${a.progress.done}/${a.progress.total}` : a.status;
    return (
        <span className={`border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${tone}`}>{label}</span>
    );
}

export default function Outputs() {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [q, setQ] = useState("");
    const [tab, setTab] = useState("all");

    const load = async () => {
        try {
            const { data } = await api.get("/api/analyses?limit=500");
            setItems(data.items);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setLoading(false);
    };

    useEffect(() => {
        load();
    }, []);

    useEffect(() => {
        if (!items.some((a) => a.status === "queued" || a.status === "processing")) return;
        const t = setInterval(load, 5000);
        return () => clearInterval(t);
    }, [items]);

    const download = async (a, fmt) => {
        const token = tokenStore.getAccess();
        const url = `${process.env.REACT_APP_BACKEND_URL}/api/analyses/${a.id}/export/${fmt}`;
        try {
            const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
            if (!resp.ok) throw new Error(`Download failed (${resp.status})`);
            const blob = await resp.blob();
            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            const base = `${a.project_name || "Quick_Analysis"}_${a.mode_label || "report"}`.replace(/[^\w-]+/g, "_");
            link.download = `${base}.${fmt === "markdown" ? "md" : fmt}`;
            link.click();
            URL.revokeObjectURL(link.href);
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const remove = async (a) => {
        if (!window.confirm(`Delete the "${a.mode_label}" report for ${a.project_name || "Quick Analysis"}? This cannot be undone.`))
            return;
        try {
            await api.delete(`/api/analyses/${a.id}`);
            setItems((prev) => prev.filter((x) => x.id !== a.id));
            toast.success("Report deleted");
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const clearQuick = async () => {
        const n = items.filter((a) => !a.project_id).length;
        if (!n) return;
        if (!window.confirm(`Delete all ${n} Quick Analysis report(s)? Project reports are kept. This cannot be undone.`))
            return;
        try {
            const { data } = await api.post("/api/analyses/bulk-delete", { scope: "quick" });
            setItems((prev) => prev.filter((a) => a.project_id));
            toast.success(`Quick analysis history cleared — ${data.deleted} report(s) removed`);
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const filtered = useMemo(
        () =>
            items.filter((i) => {
                if (tab === "project" && !i.project_id) return false;
                if (tab === "quick" && i.project_id) return false;
                if (!q) return true;
                const needle = q.toLowerCase();
                return (
                    i.mode_label?.toLowerCase().includes(needle) || i.project_name?.toLowerCase().includes(needle)
                );
            }),
        [items, tab, q],
    );
    const quickCount = items.filter((a) => !a.project_id).length;

    return (
        <div className="min-h-full bg-background">
            <div className="border-b border-ink-line bg-white px-10 py-10">
                <div className="text-overline">Reports & History</div>
                <h1 className="mt-2 font-heading text-5xl font-black leading-none tracking-tight text-navy">
                    Your reports
                </h1>
                <div className="mt-5 flex flex-wrap items-center gap-3">
                    <div className="relative w-full max-w-md">
                        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" />
                        <Input
                            data-testid="outputs-search"
                            value={q}
                            onChange={(e) => setQ(e.target.value)}
                            placeholder="Search mode or project…"
                            className="h-10 rounded-none border-ink-line pl-9 font-mono text-xs uppercase tracking-wider focus-visible:border-gold focus-visible:ring-0"
                        />
                    </div>
                    <div className="flex border border-ink-line">
                        {TABS.map((t) => (
                            <button
                                key={t.id}
                                onClick={() => setTab(t.id)}
                                data-testid={`outputs-tab-${t.id}`}
                                className={`px-4 py-2 font-mono text-[10px] uppercase tracking-wider ${
                                    tab === t.id ? "bg-navy text-gold" : "bg-white text-ink-muted hover:text-navy"
                                }`}
                            >
                                {t.label}
                            </button>
                        ))}
                    </div>
                    {quickCount > 0 && (
                        <button
                            onClick={clearQuick}
                            data-testid="clear-quick-history"
                            className="inline-flex items-center gap-2 border border-destructive bg-white px-4 py-2 font-mono text-[10px] uppercase tracking-wider text-destructive hover:bg-destructive hover:text-white"
                        >
                            <History size={12} />
                            Clear quick-analysis history ({quickCount})
                        </button>
                    )}
                </div>
            </div>

            <div className="container-steel py-10">
                {loading ? (
                    <div className="grid gap-6 md:grid-cols-2">
                        {Array.from({ length: 6 }).map((_, i) => (
                            <Skeleton key={i} className="h-40 rounded-none" />
                        ))}
                    </div>
                ) : filtered.length === 0 ? (
                    <div className="card-steel p-16 text-center">
                        <FileOutput size={48} className="mx-auto text-ink-muted" />
                        <div className="mt-6 font-heading text-2xl font-bold uppercase tracking-tight text-navy">
                            No reports here
                        </div>
                        <p className="mt-2 text-sm text-ink-muted">Run an analysis to see its report listed here.</p>
                    </div>
                ) : (
                    <div className="grid gap-6 md:grid-cols-2">
                        {filtered.map((o) => (
                            <div key={o.id} data-testid={`output-card-${o.id}`} className="card-steel flex h-full flex-col p-6">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <div className="truncate font-heading text-xl font-bold uppercase tracking-tight text-navy">
                                            {o.project_name || "Quick Analysis"}
                                        </div>
                                        <div className="mt-1 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                            {o.mode_label} · {o.created_at ? new Date(o.created_at).toLocaleString() : ""}
                                        </div>
                                        {o.takeoff_summary && (
                                            <div className="mt-1 font-mono text-[10px] uppercase tracking-wider text-navy">
                                                {(o.takeoff_summary.jurisdiction || "USA") === "USA"
                                                    ? `${Number(o.takeoff_summary.gross_short_tons || 0).toFixed(2)} short tons`
                                                    : `${Number(o.takeoff_summary.gross_t || 0).toFixed(2)} t`}{" "}
                                                · {Number(o.takeoff_summary.pieces || 0).toLocaleString()} pcs
                                            </div>
                                        )}
                                    </div>
                                    <div className="flex flex-shrink-0 items-center gap-3">
                                        <StatusPill a={o} />
                                        <Link
                                            to={`/analyses/${o.id}`}
                                            className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-gold hover:text-navy"
                                        >
                                            View <ArrowRight size={12} />
                                        </Link>
                                        <button
                                            onClick={() => remove(o)}
                                            data-testid={`output-delete-${o.id}`}
                                            className="text-ink-muted hover:text-destructive"
                                            aria-label="Delete report"
                                        >
                                            <Trash2 size={14} />
                                        </button>
                                    </div>
                                </div>
                                {o.status === "complete" ? (
                                    <div className="mt-4 flex flex-wrap gap-2">
                                        {EXPORTS.map((e) => (
                                            <button
                                                key={e.fmt}
                                                onClick={() => download(o, e.fmt)}
                                                data-testid={`output-${o.id}-${e.fmt}`}
                                                className="inline-flex items-center gap-1 border border-navy bg-white px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-navy hover:bg-navy hover:text-gold"
                                            >
                                                <Download size={12} />
                                                {e.label}
                                            </button>
                                        ))}
                                    </div>
                                ) : o.status === "failed" ? (
                                    <div className="mt-4 line-clamp-2 text-xs text-destructive">{o.error_message}</div>
                                ) : (
                                    <div className="mt-4 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                        {o.progress?.label || "Queued"}
                                    </div>
                                )}
                                {o.blockchain_hash && (
                                    <div className="mt-4 border-t border-ink-line pt-3">
                                        <BlockchainBadge hash={o.blockchain_hash} />
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
