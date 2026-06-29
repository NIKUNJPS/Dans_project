import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Download, FileOutput, Search } from "lucide-react";
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

export default function Outputs() {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [q, setQ] = useState("");

    const load = async () => {
        try {
            const { data } = await api.get("/api/outputs");
            setItems(data.items);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setLoading(false);
    };

    useEffect(() => {
        load();
    }, []);

    const download = async (aid, fmt) => {
        const token = tokenStore.getAccess();
        const url = `${process.env.REACT_APP_BACKEND_URL}/api/analyses/${aid}/export/${fmt}`;
        try {
            const resp = await fetch(url, {
                headers: { Authorization: `Bearer ${token}` },
            });
            if (!resp.ok) throw new Error(`Download failed (${resp.status})`);
            const blob = await resp.blob();
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = `report-${aid.slice(0, 8)}.${fmt === "markdown" ? "md" : fmt}`;
            a.click();
            URL.revokeObjectURL(a.href);
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const filtered = items.filter(
        (i) =>
            !q ||
            i.mode_label?.toLowerCase().includes(q.toLowerCase()) ||
            i.project_name?.toLowerCase().includes(q.toLowerCase()),
    );

    return (
        <div className="min-h-full bg-background">
            <div className="border-b border-ink-line bg-white px-10 py-10">
                <div className="text-overline">Outputs & Exports</div>
                <h1 className="mt-2 font-heading text-5xl font-black leading-none tracking-tight text-navy">
                    Your reports
                </h1>
                <div className="relative mt-5 max-w-md">
                    <Search
                        size={14}
                        className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted"
                    />
                    <Input
                        data-testid="outputs-search"
                        value={q}
                        onChange={(e) => setQ(e.target.value)}
                        placeholder="Search mode or project…"
                        className="h-10 rounded-none border-ink-line pl-9 font-mono text-xs uppercase tracking-wider focus-visible:border-gold focus-visible:ring-0"
                    />
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
                            No reports yet
                        </div>
                        <p className="mt-2 text-sm text-ink-muted">
                            Complete an analysis to see exports listed here.
                        </p>
                    </div>
                ) : (
                    <div className="grid gap-6 md:grid-cols-2">
                        {filtered.map((o) => (
                            <div
                                key={o.id}
                                data-testid={`output-card-${o.id}`}
                                className="card-steel flex h-full flex-col p-6"
                            >
                                <div className="flex items-start justify-between">
                                    <div>
                                        <div className="font-heading text-xl font-bold uppercase tracking-tight text-navy">
                                            {o.project_name || "Quick analysis"}
                                        </div>
                                        <div className="mt-1 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                            {o.mode_label}
                                        </div>
                                    </div>
                                    <Link
                                        to={`/analyses/${o.id}`}
                                        className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-gold hover:text-navy"
                                    >
                                        View <ArrowRight size={12} />
                                    </Link>
                                </div>
                                <div className="mt-4 flex flex-wrap gap-2">
                                    {EXPORTS.map((e) => (
                                        <button
                                            key={e.fmt}
                                            onClick={() => download(o.id, e.fmt)}
                                            data-testid={`output-${o.id}-${e.fmt}`}
                                            className="inline-flex items-center gap-1 border border-navy bg-white px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-navy hover:bg-navy hover:text-gold"
                                        >
                                            <Download size={12} />
                                            {e.label}
                                        </button>
                                    ))}
                                </div>
                                <div className="mt-4 border-t border-ink-line pt-3">
                                    <BlockchainBadge hash={o.blockchain_hash} />
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}