import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
    AlertOctagon,
    Calculator,
    CheckCircle2,
    ChevronRight,
    Download,
    Loader2,
    MessageSquarePlus,
    Pencil,
    Printer,
    RotateCw,
    ShieldAlert,
    ShieldCheck,
    Sparkles,
    Trash2,
    XCircle,
} from "lucide-react";
import { api, downloadFile, errMessage, fileBase } from "@/lib/api";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { BlockchainBadge } from "@/components/brand/BlockchainBadge";

const STAGE_LABELS = {
    queued: "Queued",
    processing: "Analysing",
    complete: "Complete",
    failed: "Failed",
};

const fmtNum = (v, d = 0) =>
    v === null || v === undefined || Number.isNaN(Number(v))
        ? "—"
        : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

// fmt = export route segment, ext = saved file extension.
const EXPORTS = [
    { fmt: "docx", label: "Word", ext: "docx" },
    { fmt: "pdf", label: "PDF", ext: "pdf" },
    { fmt: "xlsx", label: "Excel", ext: "xlsx" },
    { fmt: "csv", label: "CSV", ext: "csv" },
    { fmt: "markdown", label: "Markdown", ext: "md" },
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
                    `<div class="print-expand my-4 max-h-[75vh] overflow-auto border border-ink-line"><div class="no-print px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-ink-muted bg-background border-b border-ink-line sticky top-0 z-20">${tbl.rows.length.toLocaleString()} rows</div><table class="w-full text-left text-xs"><thead class="sticky top-[26px] z-10 bg-navy text-white"><tr>${tbl.header
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
            if (/^\**\s*(Table|Caption):/i.test(trim)) {
                out.push(
                    `<div class="mt-5 font-mono text-[11px] font-semibold uppercase tracking-wider text-gold">${inline(
                        trim.replace(/^\**\s*(Table|Caption):\s*/i, "").replace(/\*+$/, ""),
                    )}</div>`,
                );
                continue;
            }
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
    const nav = useNavigate();
    const [analysis, setAnalysis] = useState(null);
    const [deleting, setDeleting] = useState(false);
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
    const [downloading, setDownloading] = useState(null);
    const [renaming, setRenaming] = useState(false);
    const [titleDraft, setTitleDraft] = useState("");
    const [searchParams, setSearchParams] = useSearchParams();
    const autoPrinted = useRef(false);
    const [releaseOpen, setReleaseOpen] = useState(false);
    const [releaseReason, setReleaseReason] = useState("");
    const [releasing, setReleasing] = useState(false);
    const [creatingEstimate, setCreatingEstimate] = useState(false);

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

    const deleteAnalysis = async () => {
        if (
            !window.confirm(
                "Delete this report permanently? Its exports are removed too. This cannot be undone.",
            )
        )
            return;
        setDeleting(true);
        try {
            await api.delete(`/api/analyses/${id}`);
            toast.success("Report deleted");
            nav(analysis?.project_id ? `/projects/${analysis.project_id}` : "/outputs");
        } catch (e) {
            toast.error(errMessage(e));
            setDeleting(false);
        }
    };

    // A Master MTO / Estimation report whose quality gate failed is held: it can be
    // reviewed on screen, but downloads and prints wait for a pass or the owner's release.
    const held = analysis?.release_state === "blocked";
    const printReport = () => {
        if (held) {
            toast.error("This report is held by the quality gate — review the failed checks first.");
            return;
        }
        window.print();
    };

    // "Print" on the Reports list or a project page opens the report with ?print=1:
    // print once the report has rendered, then drop the flag so a refresh doesn't reprint.
    const printTimer = useRef(null);
    useEffect(() => {
        if (analysis?.status !== "complete" || searchParams.get("print") !== "1" || autoPrinted.current) return;
        if (analysis?.release_state === "blocked") {
            autoPrinted.current = true;
            toast.error("This report is held by the quality gate — it can be printed once it is released.");
            setSearchParams({}, { replace: true });
            return;
        }
        autoPrinted.current = true;
        printTimer.current = setTimeout(() => {
            window.print();
            setSearchParams({}, { replace: true });
        }, 600);
    }, [analysis?.status, analysis?.release_state, searchParams, setSearchParams]);
    useEffect(() => () => clearTimeout(printTimer.current), []);

    const reportName = analysis?.title || analysis?.project_name || "Quick Analysis";
    // Browsers name "Save as PDF" after the page title.
    useEffect(() => {
        if (!analysis) return undefined;
        const prev = document.title;
        document.title = `${reportName} – ${analysis.mode_label}`;
        return () => {
            document.title = prev;
        };
    }, [reportName, analysis]);
    const canEdit = analysis?.can_edit !== false;

    const saveTitle = async () => {
        const title = titleDraft.trim();
        if (!title || title === reportName) {
            setRenaming(false);
            return;
        }
        try {
            await api.patch(`/api/analyses/${id}`, { title });
            setAnalysis((a) => ({ ...a, title }));
            toast.success("Report renamed");
            setRenaming(false);
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const downloadExport = async (exp) => {
        setDownloading(exp.fmt);
        try {
            // Master MTO / Estimation: 4xtrsuct_Master_MTO_<Project> (named by the server).
            const base = analysis.download_base || fileBase(reportName, analysis.mode_label || "report");
            await downloadFile(`/api/analyses/${id}/export/${exp.fmt}`, `${base}.${exp.ext}`);
        } catch (e) {
            toast.error(errMessage(e));
        } finally {
            setDownloading(null);
        }
    };


    const releaseReport = async () => {
        setReleasing(true);
        try {
            await api.post(`/api/analyses/${id}/release`, { reason: releaseReason.trim() });
            toast.success("Report released with exceptions");
            setReleaseOpen(false);
            setReleaseReason("");
            fetchIt();
        } catch (e) {
            toast.error(errMessage(e));
        }
        setReleasing(false);
    };

    const createEstimation = async () => {
        setCreatingEstimate(true);
        try {
            const { data } = await api.post(`/api/analyses/${id}/estimation`);
            toast.success("Estimation report started — it reuses this MTO's take-off");
            nav(`/analyses/${data.id}`);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setCreatingEstimate(false);
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
            <div className="print-tight border-b border-ink-line bg-white px-10 py-8">
                <nav className="no-print mb-2 flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                    {analysis.project_id ? (
                        <>
                            <Link to="/projects" className="hover:text-navy">Projects</Link>
                            <ChevronRight size={12} />
                            <Link to={`/projects/${analysis.project_id}`} className="hover:text-navy">
                                {analysis.project_name || "Project"}
                            </Link>
                        </>
                    ) : (
                        <Link to="/outputs" className="hover:text-navy">Reports</Link>
                    )}
                    <ChevronRight size={12} />
                    <span className="text-navy">{analysis.mode_label}</span>
                </nav>

                <div className="flex flex-wrap items-end justify-between gap-6">
                    <div>
                        <div className="text-overline">{analysis.mode_label} · Analysis report</div>
                        {renaming ? (
                            <form
                                className="no-print mt-2 flex flex-wrap items-center gap-2"
                                onSubmit={(e) => {
                                    e.preventDefault();
                                    saveTitle();
                                }}
                            >
                                <input
                                    autoFocus
                                    data-testid="report-title-input"
                                    value={titleDraft}
                                    maxLength={120}
                                    onChange={(e) => setTitleDraft(e.target.value)}
                                    onKeyDown={(e) => e.key === "Escape" && setRenaming(false)}
                                    className="h-12 min-w-[320px] border border-navy px-3 font-heading text-2xl font-bold text-navy outline-none focus:border-gold"
                                />
                                <button type="submit" data-testid="report-title-save" className="btn-gold" disabled={!titleDraft.trim()}>
                                    Save
                                </button>
                                <button type="button" onClick={() => setRenaming(false)} className="btn-ghost-navy">
                                    Cancel
                                </button>
                            </form>
                        ) : (
                            <h1 className="mt-2 flex items-center gap-3 font-heading text-4xl font-black leading-none tracking-tight text-navy md:text-5xl">
                                <span data-testid="report-title">{reportName}</span>
                                {canEdit && (
                                <button
                                    onClick={() => {
                                        setTitleDraft(reportName);
                                        setRenaming(true);
                                    }}
                                    data-testid="rename-report-btn"
                                    title="Rename report"
                                    aria-label="Rename report"
                                    className="no-print text-ink-muted hover:text-gold"
                                >
                                    <Pencil size={18} />
                                </button>
                                )}
                            </h1>
                        )}
                        {analysis.title && analysis.project_id && (
                            <div className="mt-2 font-mono text-[11px] uppercase tracking-wider text-ink-muted">
                                Project: <span className="text-navy">{analysis.project_name}</span>
                            </div>
                        )}
                        <div className="mt-3 flex flex-wrap items-center gap-3 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                            <span className="print-only">STRUCTMIND AI · {analysis.mode_label}</span>
                            <span>Status: <span className="text-navy">{STAGE_LABELS[analysis.status]}</span></span>
                            {analysis.model_used && <span>Model: <span className="text-navy">{analysis.model_used}</span></span>}
                            {analysis.processing_time_seconds ? (
                                <span>Time: <span className="text-navy">{analysis.processing_time_seconds}s</span></span>
                            ) : null}
                            {analysis.completed_at && (
                                <span>Completed: <span className="text-navy">{new Date(analysis.completed_at).toLocaleString()}</span></span>
                            )}
                            {analysis.binder?.total_pages ? (
                                <span>
                                    Binder: <span className="text-navy">{analysis.binder.documents?.length || 0} docs · {analysis.binder.total_pages} pages</span>
                                </span>
                            ) : null}
                            {analysis.blockchain_hash && (
                                <span className="print-only">SHA-256 {analysis.blockchain_hash.slice(0, 16)}…</span>
                            )}
                        </div>
                    </div>

                    <div className="no-print flex flex-wrap gap-2">
                        {analysis.status === "complete" && analysis.doc_kind === "mto" && (
                            <button
                                onClick={createEstimation}
                                disabled={creatingEstimate}
                                data-testid="create-estimation-btn"
                                className="btn-gold"
                            >
                                {creatingEstimate ? <Loader2 size={14} className="animate-spin" /> : <Calculator size={14} />}
                                Create Estimation report
                            </button>
                        )}
                        {analysis.status === "complete" && (
                            <button
                                onClick={printReport}
                                disabled={held}
                                title={held ? "Held by the quality gate" : "Print this report"}
                                data-testid="print-report-btn"
                                className="btn-ghost-navy disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                <Printer size={14} />
                                Print
                            </button>
                        )}
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
                        {canEdit && (
                            <button
                                onClick={deleteAnalysis}
                                disabled={deleting}
                                data-testid="delete-analysis-btn"
                                className="inline-flex items-center gap-2 border border-destructive bg-white px-4 py-2 font-heading text-sm font-bold uppercase tracking-wider text-destructive transition-colors hover:bg-destructive hover:text-white disabled:opacity-50"
                            >
                                <Trash2 size={14} />
                                {deleting ? "Deleting…" : "Delete"}
                            </button>
                        )}
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
                            {analysis.progress?.label
                                ? `${analysis.progress.label}${
                                      analysis.progress.total > 1
                                          ? ` — batch ${Math.min(analysis.progress.done + 1, analysis.progress.total)} of ${analysis.progress.total}`
                                          : ""
                                  }`
                                : polling
                                  ? "Preparing the binder…"
                                  : "Queued."}
                        </p>
                        {analysis.progress?.total > 0 && (
                            <div className="mt-5">
                                <div className="h-2 w-full border border-ink-line bg-background">
                                    <div
                                        className="h-full bg-gold transition-all duration-700"
                                        style={{
                                            width: `${Math.max(
                                                4,
                                                Math.round((100 * (analysis.progress.done || 0)) / analysis.progress.total),
                                            )}%`,
                                        }}
                                    />
                                </div>
                                <div className="mt-2 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                    Large binders are read in page batches in parallel and consolidated into one report.
                                </div>
                            </div>
                        )}
                        <div className="mt-8 grid grid-cols-4 gap-0 border border-ink-line">
                            {["queued", "reading", "consolidating", "complete"].map((s, i) => {
                                const consolidating = /consolidat|computing/i.test(analysis.progress?.label || "");
                                const done =
                                    (analysis.status === "processing" && (i <= 1 || (i === 2 && consolidating))) ||
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
                    <ReportSummary analysis={analysis} />

                    {analysis.quality_gate && (
                        <QualityGatePanel
                            analysis={analysis}
                            onRelease={() => setReleaseOpen(true)}
                        />
                    )}

                    {/* Exports + Blockchain */}
                    <div className="no-print card-steel mt-6 p-6">
                        <div className="flex flex-wrap items-center justify-between gap-4">
                            <div>
                                <div className="text-overline">Exports</div>
                                <div className="mt-1 font-heading text-lg font-bold uppercase tracking-tight text-navy">
                                    Download report · SHA-256 anchored
                                </div>
                                {analysis.download_base && (
                                    <div className="mt-1 font-mono text-[10px] tracking-wider text-ink-muted" data-testid="download-base">
                                        {analysis.download_base}.docx
                                    </div>
                                )}
                                {held && (
                                    <div className="mt-1 font-mono text-[10px] uppercase tracking-wider text-destructive">
                                        Downloads are held until the quality gate passes or the owner releases the report.
                                    </div>
                                )}
                            </div>
                            <BlockchainBadge hash={analysis.blockchain_hash} />
                        </div>
                        <div className="mt-5 flex flex-wrap gap-2">
                            {EXPORTS.map((e) => (
                                <button
                                    key={e.fmt}
                                    data-testid={`export-${e.fmt}`}
                                    onClick={() => downloadExport(e)}
                                    disabled={downloading !== null || held}
                                    title={held ? "Held by the quality gate" : `Download ${e.label}`}
                                    className="inline-flex items-center gap-2 border border-navy bg-white px-4 py-2 font-heading text-sm font-bold uppercase tracking-wider text-navy transition-colors hover:bg-navy hover:text-gold disabled:cursor-wait disabled:opacity-60"
                                >
                                    {downloading === e.fmt ? (
                                        <Loader2 size={14} className="animate-spin" />
                                    ) : (
                                        <Download size={14} />
                                    )}
                                    {e.label}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Sections */}
                    <div className="mt-6 grid gap-6 lg:grid-cols-4">
                        <aside className="no-print lg:col-span-1">
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
                        <div className="print-full lg:col-span-3 space-y-6">
                            {sections.map((s, i) => (
                                <section
                                    key={s.id}
                                    id={`sec-${s.id}`}
                                    className="print-section card-steel p-6"
                                >
                                    <div className="flex items-center justify-between border-b border-ink-line pb-3">
                                        <div className="flex items-center gap-3">
                                            <div className="font-mono text-xs uppercase tracking-wider text-gold">
                                                {String(i + 1).padStart(2, "0")}
                                            </div>
                                            <div className="section-title font-heading text-xl font-bold uppercase tracking-tight text-navy">
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

            {/* Release-with-exceptions modal */}
            {releaseOpen && (
                <div
                    className="no-print fixed inset-0 z-50 flex items-center justify-center bg-navy/60 px-4"
                    onClick={() => setReleaseOpen(false)}
                >
                    <div
                        className="w-full max-w-lg border border-ink-line bg-white p-6"
                        onClick={(e) => e.stopPropagation()}
                        data-testid="release-dialog"
                    >
                        <div className="text-overline">Quality gate</div>
                        <div className="mt-1 font-heading text-2xl font-black uppercase tracking-tight text-navy">
                            Release with exceptions
                        </div>
                        <p className="mt-2 text-sm text-ink-muted">
                            The failed checks and your reason are recorded on the report and in the audit log.
                        </p>
                        <ul className="mt-3 space-y-1 text-sm">
                            {(analysis.quality_gate?.checks || [])
                                .filter((c) => !c.passed)
                                .map((c) => (
                                    <li key={c.id} className="flex items-start gap-2 text-destructive">
                                        <XCircle size={14} className="mt-0.5 shrink-0" /> {c.id}. {c.name}
                                    </li>
                                ))}
                        </ul>
                        <textarea
                            data-testid="release-reason"
                            value={releaseReason}
                            onChange={(e) => setReleaseReason(e.target.value)}
                            maxLength={1000}
                            rows={4}
                            placeholder="Why is this report fit to issue? (at least 10 characters)"
                            className="mt-4 w-full border border-ink-line p-3 text-sm outline-none focus:border-gold"
                        />
                        <div className="mt-4 flex justify-end gap-2">
                            <button onClick={() => setReleaseOpen(false)} className="btn-ghost-navy">
                                Cancel
                            </button>
                            <button
                                onClick={releaseReport}
                                disabled={releasing || releaseReason.trim().length < 10}
                                data-testid="release-confirm"
                                className="btn-gold disabled:opacity-50"
                            >
                                {releasing ? "Releasing…" : "Release report"}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* RFI Modal */}
            {rfiOpen && (
                <div
                    className="no-print fixed inset-0 z-50 flex items-center justify-center bg-navy/60 px-4"
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
function SummaryTile({ label, value, sub }) {
    return (
        <div className="border border-ink-line bg-white p-4">
            <div className="border-t-2 border-gold pt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                {label}
            </div>
            <div className="mt-1 font-heading text-2xl font-bold text-navy">{value}</div>
            {sub && (
                <div className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-ink-muted">{sub}</div>
            )}
        </div>
    );
}

// Distinct RFI ids in a narrative report ("RFI-001", "RFI-014", …).
function countRfis(md) {
    return new Set((md || "").match(/\bRFI-\d{2,4}\b/g) || []).size;
}

function QualityGatePanel({ analysis, onRelease }) {
    const gate = analysis.quality_gate;
    const checks = gate.checks || [];
    const passed = checks.filter((c) => c.passed).length;
    const state = analysis.release_state;
    const rel = analysis.release;
    const [open, setOpen] = useState(state === "blocked");
    const tone =
        state === "blocked"
            ? "border-l-destructive"
            : state === "released_with_exceptions"
              ? "border-l-gold"
              : "border-l-emerald-600";
    return (
        <div className={`print-section card-steel mt-6 border-l-4 p-6 ${tone}`} data-testid="quality-gate">
            <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                    {state === "blocked" ? (
                        <ShieldAlert size={22} className="text-destructive" />
                    ) : (
                        <ShieldCheck size={22} className={state === "released" ? "text-emerald-600" : "text-gold"} />
                    )}
                    <div>
                        <div className="text-overline">Quality gate</div>
                        <div className="font-heading text-lg font-bold uppercase tracking-tight text-navy" data-testid="quality-gate-state">
                            {state === "blocked"
                                ? `Held for review · ${passed} of ${checks.length} checks passed`
                                : state === "released_with_exceptions"
                                  ? `Released with exceptions · ${passed} of ${checks.length} checks passed`
                                  : `Released · all ${checks.length} checks passed`}
                        </div>
                    </div>
                </div>
                <div className="no-print flex gap-2">
                    <button onClick={() => setOpen((o) => !o)} className="btn-ghost-navy" data-testid="quality-gate-toggle">
                        {open ? "Hide checks" : "Show checks"}
                    </button>
                    {analysis.can_release && (
                        <button onClick={onRelease} className="btn-gold" data-testid="release-report-btn">
                            Release with exceptions
                        </button>
                    )}
                </div>
            </div>
            {rel && (
                <div className="mt-3 text-sm text-ink-muted">
                    Released by <span className="text-navy">{rel.by_name || "owner"}</span>
                    {rel.at ? ` on ${new Date(rel.at).toLocaleString()}` : ""} — “{rel.reason}”
                </div>
            )}
            {open && (
                <ul className="mt-4 divide-y divide-ink-line border border-ink-line">
                    {checks.map((c) => (
                        <li key={c.id} className="flex items-start gap-3 p-3" data-testid={`gate-check-${c.id}`}>
                            {c.passed ? (
                                <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-600" />
                            ) : (
                                <XCircle size={16} className="mt-0.5 shrink-0 text-destructive" />
                            )}
                            <div className="min-w-0">
                                <div className="font-heading text-sm font-bold uppercase tracking-wide text-navy">
                                    {c.id}. {c.name}
                                </div>
                                {c.detail && <div className="text-sm text-ink-muted">{c.detail}</div>}
                                {!c.passed && c.items?.length > 0 && (
                                    <ul className="mt-1 list-disc pl-5 font-mono text-[11px] text-ink">
                                        {c.items.slice(0, 12).map((it, i) => (
                                            <li key={i} className="break-words">{it}</li>
                                        ))}
                                        {c.items.length > 12 && <li>… {c.items.length - 12} more</li>}
                                    </ul>
                                )}
                            </div>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

function ReportSummary({ analysis }) {
    const t = analysis.takeoff_summary;
    const e = analysis.estimate_summary;
    const usa = (t?.jurisdiction || "USA") === "USA";
    const tiles = [];
    if (t) {
        const tons = t.est_tonnage ?? (usa ? t.gross_short_tons : t.gross_t);
        tiles.push(
            <SummaryTile
                key="tons"
                label="Estimated Tonnage"
                value={usa ? `${fmtNum(tons, 2)} tons` : `${fmtNum(tons, 2)} t`}
                sub={
                    usa
                        ? `2,000 lb basis · ${fmtNum(t.gross_lb ?? (t.gross_kg || 0) * 2.20462)} lbs`
                        : `${fmtNum(t.gross_kg)} kg`
                }
            />,
        );
    }
    if (e?.lock) {
        const sym = e.currency === "USD" ? "$" : `${e.currency} `;
        tiles.push(
            <SummaryTile
                key="est"
                label="Estimate (mid, inc. tax)"
                value={`${sym}${fmtNum(e.lock.mid)}`}
                sub={`${sym}${fmtNum(e.lock.low)} – ${sym}${fmtNum(e.lock.high)}`}
            />,
        );
    }
    if (e?.lock_hrs_low !== undefined) {
        tiles.push(
            <SummaryTile
                key="hrs"
                label="Detailing hours"
                value={`${fmtNum(e.lock_hrs_low)} – ${fmtNum(e.lock_hrs_high)}`}
                sub={`${e.currency} ${fmtNum(e.lock_cost_low)} – ${fmtNum(e.lock_cost_high)}`}
            />,
        );
    }
    const rfis = t ? t.rfis : countRfis(analysis.output_markdown);
    if (t || rfis) {
        tiles.push(<SummaryTile key="rfi" label="RFIs" value={fmtNum(rfis)} sub="listed in the RFI package" />);
    }
    if (!tiles.length) return null;
    return <div className={`grid gap-4 sm:grid-cols-2 ${tiles.length > 2 ? "lg:grid-cols-3" : ""}`}>{tiles}</div>;
}
