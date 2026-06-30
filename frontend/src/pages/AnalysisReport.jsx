import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
    AlertOctagon,
    ChevronRight,
    Download,
    MessageSquarePlus,
    RotateCw,
    Sparkles,
} from "lucide-react";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { BlockchainBadge } from "@/components/brand/BlockchainBadge";
import { tokenStore } from "@/lib/auth";

const STAGE_LABELS = {
    queued: "Queued",
    processing: "Analysing",
    complete: "Complete",
    failed: "Failed",
};

const EXPORTS = [
    { fmt: "docx", label: "Word" },
    { fmt: "pdf", label: "PDF" },
    { fmt: "xlsx", label: "Excel" },
    { fmt: "csv", label: "CSV" },
    { fmt: "markdown", label: "Markdown" },
];

function parseMarkdownSections(md) {
    if (!md) return [];
    const blocks = md.split(/\n(?=#{1,3}\s)/);
    return blocks
        .map((b, i) => {
            const firstLine = b.split("\n")[0];
            const title = firstLine.replace(/^#{1,3}\s*/, "").trim();
            const body = b.replace(/^#{1,3}.*\n/, "").trim();
            return { id: i, title: title || `Section ${i + 1}`, body };
        })
        .filter((s) => s.body || s.title);
}

function MarkdownBlock({ body }) {
    const html = useMemo(() => {
        const lines = (body || "").split("\n");
        const out = [];
        let tbl = null;
        let listType = null;
        let list = [];
        const flushList = () => {
            if (list.length) {
                if (listType === "ul")
                    out.push(
                        `<ul class="ml-6 mt-2 list-disc space-y-1 text-sm text-ink">${list
                            .map((x) => `<li>${x}</li>`)
                            .join("")}</ul>`,
                    );
                else
                    out.push(
                        `<ol class="ml-6 mt-2 list-decimal space-y-1 text-sm text-ink">${list
                            .map((x) => `<li>${x}</li>`)
                            .join("")}</ol>`,
                    );
                list = [];
                listType = null;
            }
        };
        const flushTable = () => {
            if (tbl) {
                out.push(
                    `<div class="my-4 max-h-[75vh] overflow-auto border border-ink-line"><div class="px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-ink-muted bg-background border-b border-ink-line sticky top-0 z-20">${tbl.rows.length.toLocaleString()} rows</div><table class="w-full text-left text-xs"><thead class="sticky top-[26px] z-10 bg-navy text-white"><tr>${tbl.header
                        .map(
                            (h) =>
                                `<th class="border-r border-white/10 px-3 py-2 font-heading text-xs uppercase tracking-wider">${inline(h)}</th>`,
                        )
                        .join(
                            "",
                        )}</tr></thead><tbody>${tbl.rows
                        .map(
                            (r) =>
                                `<tr class="border-t border-ink-line bg-white even:bg-background">${r
                                    .map(
                                        (c) =>
                                            `<td class="border-r border-ink-line px-3 py-2 text-ink">${inline(
                                                c,
                                            )}</td>`,
                                    )
                                    .join("")}</tr>`,
                        )
                        .join("")}</tbody></table></div>`,
                );
                tbl = null;
            }
        };
        const inline = (s) =>
            s
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
                .replace(/\*(.+?)\*/g, "<em>$1</em>")
                .replace(/`([^`]+)`/g, '<code class="bg-gold-pale px-1 font-mono text-xs">$1</code>');

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            const trim = line.trim();
            if (!trim) {
                flushList();
                continue;
            }
            if (trim.startsWith("|")) {
                flushList();
                const next = (lines[i + 1] || "").trim();
                if (!tbl && /^\|[\s\-:|]+\|$/.test(next)) {
                    tbl = {
                        header: trim.slice(1, -1).split("|").map((x) => x.trim()),
                        rows: [],
                    };
                    i++;
                    continue;
                }
                if (tbl) {
                    tbl.rows.push(trim.slice(1, -1).split("|").map((x) => x.trim()));
                    continue;
                }
            } else {
                flushTable();
            }
            if (/^[-*]\s/.test(trim)) {
                if (listType !== "ul") flushList();
                listType = "ul";
                list.push(inline(trim.slice(2)));
                continue;
            }
            if (/^\d+\.\s/.test(trim)) {
                if (listType !== "ol") flushList();
                listType = "ol";
                list.push(inline(trim.replace(/^\d+\.\s/, "")));
                continue;
            }
            if (/^#{4,}\s/.test(trim)) {
                flushList();
                out.push(
                    `<h5 class="mt-4 font-heading text-sm font-semibold uppercase tracking-tight text-navy">${inline(
                        trim.replace(/^#+\s/, ""),
                    )}</h5>`,
                );
                continue;
            }
            flushList();
            out.push(`<p class="text-sm leading-relaxed text-ink">${inline(trim)}</p>`);
        }
        flushList();
        flushTable();
        return out.join("\n");
    }, [body]);
    return (
        <div
            className="analysis-body space-y-2"
            dangerouslySetInnerHTML={{ __html: html }}
        />
    );
}

export default function AnalysisReport() {
    const { id } = useParams();
    const [analysis, setAnalysis] = useState(null);
    const [loading, setLoading] = useState(true);
    const [polling, setPolling] = useState(false);
    const [rfiOpen, setRfiOpen] = useState(false);
    const [rfiForm, setRfiForm] = useState({
        subject: "",
        body: "",
        priority: "standard",
        sheet_reference: "",
        blocking: false,
    });
    const [creatingRfi, setCreatingRfi] = useState(false);

    const fetchIt = async () => {
        try {
            const { data } = await api.get(`/api/analyses/${id}`);
            setAnalysis(data);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setLoading(false);
    };

    useEffect(() => {
        fetchIt();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [id]);

    useEffect(() => {
        if (!analysis) return;
        if (analysis.status === "complete" || analysis.status === "failed") return;
        setPolling(true);
        const t = setInterval(fetchIt, 2500);
        return () => {
            clearInterval(t);
            setPolling(false);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [analysis?.status]);

    const rerun = async () => {
        try {
            await api.post(`/api/analyses/${id}/rerun`);
            toast.success("Re-queued.");
            fetchIt();
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const downloadExport = async (fmt) => {
        try {
            const token = tokenStore.getAccess();
            const url = `${process.env.REACT_APP_BACKEND_URL}/api/analyses/${id}/export/${fmt}`;
            const resp = await fetch(url, {
                headers: { Authorization: `Bearer ${token}` },
            });
            if (!resp.ok) {
                throw new Error(`Download failed (${resp.status})`);
            }
            const blob = await resp.blob();
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            const baseName = (analysis.project_name || analysis.mode_label || "report").replace(/\s+/g, "_");
            a.download = `${baseName}.${fmt === "markdown" ? "md" : fmt}`;
            a.click();
            URL.revokeObjectURL(a.href);
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const createRfi = async () => {
        setCreatingRfi(true);
        try {
            await api.post("/api/rfis", {
                ...rfiForm,
                project_id: analysis?.project_id || null,
                analysis_id: analysis?.id,
            });
            toast.success("RFI created");
            setRfiOpen(false);
            setRfiForm({
                subject: "",
                body: "",
                priority: "standard",
                sheet_reference: "",
                blocking: false,
            });
        } catch (e) {
            toast.error(errMessage(e));
        }
        setCreatingRfi(false);
    };

    if (loading) {
        return <Skeleton className="m-10 h-96 rounded-none" />;
    }
    if (!analysis) return null;

    const sections = parseMarkdownSections(analysis.output_markdown);

    return (
        <div className="min-h-full bg-background">
            <div className="border-b border-ink-line bg-white px-10 py-8">
                <nav className="mb-2 flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                    <Link to="/outputs" className="hover:text-navy">Outputs</Link>
                    <ChevronRight size={12} />
                    <span className="text-navy">{analysis.project_name || analysis.mode_label}</span>
                </nav>

                <div className="flex flex-wrap items-end justify-between gap-6">
                    <div>
                        <div className="text-overline">{analysis.mode_label} · Analysis report</div>
                        <h1 className="mt-2 font-heading text-4xl font-black leading-none tracking-tight text-navy md:text-5xl">
                            {analysis.project_name || analysis.mode_label}
                        </h1>
                        <div className="mt-3 flex flex-wrap items-center gap-3 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                            <span>Status: <span className="text-navy">{STAGE_LABELS[analysis.status]}</span></span>
                            {analysis.model_used && <span>Model: <span className="text-navy">{analysis.model_used}</span></span>}
                            {analysis.processing_time_seconds ? (
                                <span>Time: <span className="text-navy">{analysis.processing_time_seconds}s</span></span>
                            ) : null}
                            {analysis.completed_at && (
                                <span>Completed: <span className="text-navy">{new Date(analysis.completed_at).toLocaleString()}</span></span>
                            )}
                        </div>
                    </div>

                    <div className="flex flex-wrap gap-2">
                        <button
                            onClick={() => setRfiOpen(true)}
                            data-testid="create-rfi-btn"
                            className="btn-ghost-navy"
                        >
                            <MessageSquarePlus size={14} />
                            Create RFI
                        </button>
                        <button
                            onClick={rerun}
                            data-testid="rerun-analysis-btn"
                            className="btn-ghost-navy"
                        >
                            <RotateCw size={14} />
                            Re-run
                        </button>
                    </div>
                </div>
            </div>

            {/* Processing view */}
            {analysis.status !== "complete" && analysis.status !== "failed" && (
                <div className="container-steel py-10">
                    <div className="card-steel p-10">
                        <div className="flex items-center gap-3 font-heading text-xl font-bold uppercase tracking-tight text-navy">
                            <Sparkles size={20} className="animate-pulse text-gold" />
                            Running · {analysis.mode_label}
                        </div>
                        <p className="mt-2 text-sm text-ink-muted">
                            {polling
                                ? "Live-polling backend every 2.5s. Hold tight — this is usually quick."
                                : "Queued."}
                        </p>
                        <div className="mt-8 grid grid-cols-4 gap-0 border border-ink-line">
                            {["queued", "processing", "finalising", "complete"].map((s, i) => {
                                const done =
                                    (analysis.status === "processing" && i <= 1) ||
                                    (analysis.status === "complete" && i <= 3);
                                return (
                                    <div
                                        key={s}
                                        className={`border-r border-ink-line p-4 text-center last:border-r-0 ${
                                            done ? "bg-navy text-gold" : "bg-white text-ink-muted"
                                        }`}
                                    >
                                        <div className="font-mono text-[9px] uppercase tracking-wider">
                                            STEP {i + 1}
                                        </div>
                                        <div className="mt-1 font-heading text-base font-bold uppercase">
                                            {s}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </div>
            )}

            {analysis.status === "failed" && (
                <div className="container-steel py-10">
                    <div className="card-steel border-l-4 border-l-destructive p-8">
                        <div className="flex items-center gap-2 font-heading text-xl font-bold uppercase tracking-tight text-destructive">
                            <AlertOctagon size={20} />
                            Analysis failed
                        </div>
                        <p className="mt-2 text-sm text-ink-muted">
                            {analysis.error_message || "Unknown error"}
                        </p>
                        <button onClick={rerun} className="btn-gold mt-4">
                            <RotateCw size={14} /> Try again
                        </button>
                    </div>
                </div>
            )}

            {analysis.status === "complete" && (
                <div className="container-steel py-10">
                    {/* Exports + Blockchain */}
                    <div className="card-steel p-6">
                        <div className="flex flex-wrap items-center justify-between gap-4">
                            <div>
                                <div className="text-overline">Exports</div>
                                <div className="mt-1 font-heading text-lg font-bold uppercase tracking-tight text-navy">
                                    All formats generated · SHA-256 anchored
                                </div>
                            </div>
                            <BlockchainBadge hash={analysis.blockchain_hash} />
                        </div>
                        <div className="mt-5 flex flex-wrap gap-2">
                            {EXPORTS.map((e) => (
                                <button
                                    key={e.fmt}
                                    data-testid={`export-${e.fmt}`}
                                    onClick={() => downloadExport(e.fmt)}
                                    className="inline-flex items-center gap-2 border border-navy bg-white px-4 py-2 font-heading text-sm font-bold uppercase tracking-wider text-navy transition-colors hover:bg-navy hover:text-gold"
                                >
                                    <Download size={14} />
                                    {e.label}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Sections */}
                    <div className="mt-6 grid gap-6 lg:grid-cols-4">
                        <aside className="lg:col-span-1">
                            <div className="sticky top-6 card-steel p-4">
                                <div className="text-overline mb-3">Contents</div>
                                <ul className="space-y-1">
                                    {sections.map((s, i) => (
                                        <li key={s.id}>
                                            <a
                                                href={`#sec-${s.id}`}
                                                className="block truncate border-l-2 border-transparent py-1 pl-2 font-mono text-[11px] uppercase tracking-wider text-ink-muted hover:border-gold hover:text-navy"
                                            >
                                                {String(i + 1).padStart(2, "0")} · {s.title}
                                            </a>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        </aside>
                        <div className="lg:col-span-3 space-y-6">
                            {sections.map((s, i) => (
                                <section
                                    key={s.id}
                                    id={`sec-${s.id}`}
                                    className="card-steel p-6"
                                >
                                    <div className="flex items-center justify-between border-b border-ink-line pb-3">
                                        <div className="flex items-center gap-3">
                                            <div className="font-mono text-xs uppercase tracking-wider text-gold">
                                                {String(i + 1).padStart(2, "0")}
                                            </div>
                                            <div className="font-heading text-xl font-bold uppercase tracking-tight text-navy">
                                                {s.title}
                                            </div>
                                        </div>
                                    </div>
                                    <div className="mt-4">
                                        <MarkdownBlock body={s.body} />
                                    </div>
                                </section>
                            ))}
                        </div>
                    </div>
                </div>
            )}

            {/* RFI Modal */}
            {rfiOpen && (
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-navy/60 px-4"
                    onClick={() => setRfiOpen(false)}
                >
                    <div
                        className="card-steel w-full max-w-lg"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="flex items-center justify-between border-b border-ink-line bg-navy px-5 py-3">
                            <div className="font-heading text-lg font-bold uppercase tracking-wide text-white">
                                Create RFI
                            </div>
                            <button onClick={() => setRfiOpen(false)} className="text-white/70 hover:text-gold">
                                ✕
                            </button>
                        </div>
                        <div className="space-y-3 p-5">
                            <input
                                data-testid="rfi-subject"
                                placeholder="Subject"
                                value={rfiForm.subject}
                                onChange={(e) => setRfiForm((f) => ({ ...f, subject: e.target.value }))}
                                className="h-11 w-full border border-ink-line bg-white px-3 font-body text-sm focus-visible:border-gold focus-visible:outline-none"
                            />
                            <input
                                placeholder="Sheet reference (S-201)"
                                value={rfiForm.sheet_reference}
                                onChange={(e) => setRfiForm((f) => ({ ...f, sheet_reference: e.target.value }))}
                                className="h-11 w-full border border-ink-line bg-white px-3 font-body text-sm focus-visible:border-gold focus-visible:outline-none"
                            />
                            <textarea
                                data-testid="rfi-body"
                                placeholder="Describe the RFI…"
                                rows={5}
                                value={rfiForm.body}
                                onChange={(e) => setRfiForm((f) => ({ ...f, body: e.target.value }))}
                                className="w-full border border-ink-line bg-white p-3 font-body text-sm focus-visible:border-gold focus-visible:outline-none"
                            />
                            <div className="flex items-center gap-3">
                                <select
                                    value={rfiForm.priority}
                                    onChange={(e) => setRfiForm((f) => ({ ...f, priority: e.target.value }))}
                                    className="h-11 border border-ink-line bg-white px-3 font-mono text-xs uppercase tracking-wider"
                                >
                                    <option value="standard">Standard</option>
                                    <option value="urgent">Urgent</option>
                                    <option value="critical">Critical</option>
                                </select>
                                <label className="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-wider text-ink-muted">
                                    <input
                                        type="checkbox"
                                        checked={rfiForm.blocking}
                                        onChange={(e) => setRfiForm((f) => ({ ...f, blocking: e.target.checked }))}
                                    />
                                    Blocking
                                </label>
                            </div>
                        </div>
                        <div className="flex items-center gap-2 border-t border-ink-line bg-background px-5 py-3">
                            <button
                                onClick={() => setRfiOpen(false)}
                                className="btn-ghost-navy flex-1"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={createRfi}
                                data-testid="submit-rfi-btn"
                                disabled={creatingRfi || !rfiForm.subject || !rfiForm.body}
                                className="btn-gold flex-1"
                            >
                                {creatingRfi ? "Creating…" : "Create RFI"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}