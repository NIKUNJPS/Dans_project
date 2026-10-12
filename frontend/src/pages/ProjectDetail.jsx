import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
    ArrowRight,
    ChevronRight,
    FileIcon,
    History,
    MessageSquareQuote,
    Pencil,
    Printer,
    Sparkles,
    Trash2,
    Users,
} from "lucide-react";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { ProjectEditDialog, deleteProjectForever } from "@/components/projects/ProjectEditDialog";

export default function ProjectDetail() {
    const { id } = useParams();
    const nav = useNavigate();
    const [project, setProject] = useState(null);
    const [files, setFiles] = useState([]);
    const [analyses, setAnalyses] = useState([]);
    const [rfis, setRfis] = useState([]);
    const [loading, setLoading] = useState(true);
    const [editOpen, setEditOpen] = useState(false);

    const load = async () => {
        try {
            const [p, f, a, r] = await Promise.all([
                api.get(`/api/projects/${id}`),
                api.get(`/api/files?project_id=${id}`),
                api.get(`/api/analyses?project_id=${id}`),
                api.get(`/api/rfis?project_id=${id}`),
            ]);
            setProject(p.data);
            setFiles(f.data.items);
            setAnalyses(a.data.items);
            setRfis(r.data.items);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setLoading(false);
    };

    useEffect(() => {
        load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [id]);

    // Keep running analyses' status fresh without a manual reload.
    useEffect(() => {
        if (!analyses.some((a) => a.status === "queued" || a.status === "processing")) return;
        const t = setInterval(async () => {
            try {
                const { data } = await api.get(`/api/analyses?project_id=${id}`);
                setAnalyses(data.items);
            } catch {}
        }, 5000);
        return () => clearInterval(t);
    }, [analyses, id]);

    const deleteAnalysis = async (a) => {
        if (!window.confirm(`Delete the "${a.mode_label || a.mode}" report? This cannot be undone.`)) return;
        try {
            await api.delete(`/api/analyses/${a.id}`);
            setAnalyses((prev) => prev.filter((x) => x.id !== a.id));
            toast.success("Report deleted");
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const clearHistory = async () => {
        if (
            !window.confirm(
                `Delete ALL ${analyses.length} report(s) and estimates in this project's history? Uploaded drawings are kept. This cannot be undone.`,
            )
        )
            return;
        try {
            const { data } = await api.post("/api/analyses/bulk-delete", { project_id: id });
            setAnalyses([]);
            toast.success(`History cleared — ${data.deleted} report(s) removed`);
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const deleteFile = async (f) => {
        if (!window.confirm(`Delete "${f.original_name}" from this project?`)) return;
        try {
            await api.delete(`/api/files/${f.id}`);
            setFiles((prev) => prev.filter((x) => x.id !== f.id));
            toast.success("File deleted");
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const deleteProject = async () => {
        if (await deleteProjectForever(project)) nav("/projects");
    };

    const onProjectSaved = (p) => {
        setProject(p);
        // Reports in this project now carry the new name.
        setAnalyses((list) => list.map((a) => ({ ...a, project_name: p.name })));
    };

    if (loading) return <Skeleton className="m-10 h-96 rounded-none" />;
    if (!project) return null;

    return (
        <div className="min-h-full bg-background">
            <div className="border-b border-ink-line bg-white px-10 py-10">
                <nav className="mb-3 flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                    <Link to="/projects" className="hover:text-navy">Projects</Link>
                    <ChevronRight size={12} />
                    <span className="text-navy">{project.name}</span>
                </nav>
                <div className="flex items-end justify-between">
                    <div>
                        <div className="text-overline">Project</div>
                        <h1 className="mt-2 font-heading text-5xl font-black leading-none tracking-tight text-navy">
                            {project.name}
                        </h1>
                        <p className="mt-3 max-w-2xl text-ink-muted">
                            {project.description || "No description provided."}
                        </p>
                        {project.tags?.length > 0 && (
                            <div className="mt-3 flex flex-wrap gap-2">
                                {project.tags.map((t) => (
                                    <span
                                        key={t}
                                        className="border border-ink-line bg-background px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-ink-muted"
                                    >
                                        {t}
                                    </span>
                                ))}
                            </div>
                        )}
                    </div>
                    <div className="flex flex-wrap gap-3">
                        <button
                            onClick={() => setEditOpen(true)}
                            data-testid="project-edit-btn"
                            className="btn-ghost-navy"
                        >
                            <Pencil size={14} />
                            Edit project
                        </button>
                        <button
                            onClick={deleteProject}
                            data-testid="project-delete-btn"
                            className="inline-flex items-center gap-2 border border-destructive bg-white px-4 py-2 font-heading text-sm font-bold uppercase tracking-wider text-destructive transition-colors hover:bg-destructive hover:text-white"
                        >
                            <Trash2 size={14} />
                            Delete project
                        </button>
                        <Link
                            to={`/projects/${id}/analyze`}
                            data-testid="project-analyze-btn"
                            className="btn-gold"
                        >
                            <Sparkles size={14} />
                            Run analysis
                            <ArrowRight size={14} />
                        </Link>
                    </div>
                </div>
            </div>

            <div className="container-steel py-10">
                <div className="grid gap-6 md:grid-cols-4">
                    <Stat
                        icon={FileIcon}
                        label="Files"
                        value={files.length}
                    />
                    <Stat
                        icon={Sparkles}
                        label="Analyses"
                        value={analyses.length}
                    />
                    <Stat
                        icon={MessageSquareQuote}
                        label="RFIs"
                        value={rfis.length}
                    />
                    <Stat
                        icon={Users}
                        label="Team"
                        value={project.team_members?.length ?? 0}
                    />
                </div>

                <div className="mt-8 grid gap-6 md:grid-cols-2">
                    <SectionBlock
                        title="Files"
                        count={files.length}
                        emptyText="No files uploaded yet."
                        action={
                            <Link
                                to={`/projects/${id}/analyze`}
                                className="font-mono text-[10px] uppercase tracking-wider text-gold hover:text-navy"
                            >
                                Upload → run analysis
                            </Link>
                        }
                    >
                        <div className="max-h-[420px] overflow-y-auto">
                            {files.map((f) => (
                                <div
                                    key={f.id}
                                    data-testid={`project-file-${f.id}`}
                                    className="flex items-center justify-between gap-3 border-b border-ink-line py-2 last:border-b-0"
                                >
                                    <div className="min-w-0 flex-1 truncate text-sm text-navy">
                                        {f.original_name}
                                    </div>
                                    <div className="font-mono text-[10px] uppercase text-ink-muted">
                                        {f.size_mb} MB
                                    </div>
                                    <button
                                        onClick={() => deleteFile(f)}
                                        className="text-ink-muted hover:text-destructive"
                                        aria-label={`Delete ${f.original_name}`}
                                    >
                                        <Trash2 size={14} />
                                    </button>
                                </div>
                            ))}
                        </div>
                    </SectionBlock>

                    <SectionBlock
                        title="Report history"
                        count={analyses.length}
                        emptyText="No analyses yet."
                        action={
                            analyses.length > 0 && (
                                <button
                                    onClick={clearHistory}
                                    data-testid="project-clear-history"
                                    className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-destructive hover:underline"
                                >
                                    <History size={12} />
                                    Clear history
                                </button>
                            )
                        }
                    >
                        <div className="max-h-[420px] overflow-y-auto">
                            {analyses.map((a) => (
                                <div
                                    key={a.id}
                                    data-testid={`project-analysis-${a.id}`}
                                    className="flex items-center justify-between gap-3 border-b border-ink-line py-2 last:border-b-0"
                                >
                                    <Link to={`/analyses/${a.id}`} className="min-w-0 flex-1 hover:text-gold">
                                        <div className="truncate font-heading text-sm font-semibold uppercase tracking-wide text-navy hover:text-gold">
                                            {a.title || a.mode_label || a.mode}
                                        </div>
                                        <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                            {a.title ? `${a.mode_label} · ` : ""}
                                            {a.created_at ? new Date(a.created_at).toLocaleString() : ""}
                                            {a.takeoff_summary
                                                ? ` · ${(a.takeoff_summary.jurisdiction || "USA") === "USA"
                                                      ? `${Number(a.takeoff_summary.gross_short_tons || 0).toFixed(2)} sh tons`
                                                      : `${Number(a.takeoff_summary.gross_t || 0).toFixed(2)} t`}`
                                                : ""}
                                        </div>
                                    </Link>
                                    <StatusPill status={a.status} progress={a.progress} />
                                    {a.status === "complete" && (
                                        <Link
                                            to={`/analyses/${a.id}?print=1`}
                                            data-testid={`project-analysis-print-${a.id}`}
                                            className="text-ink-muted hover:text-navy"
                                            aria-label="Print report"
                                            title="Print report"
                                        >
                                            <Printer size={14} />
                                        </Link>
                                    )}
                                    <button
                                        onClick={() => deleteAnalysis(a)}
                                        className="text-ink-muted hover:text-destructive"
                                        aria-label="Delete report"
                                    >
                                        <Trash2 size={14} />
                                    </button>
                                </div>
                            ))}
                        </div>
                    </SectionBlock>
                </div>
            </div>
            <ProjectEditDialog project={project} open={editOpen} onOpenChange={setEditOpen} onSaved={onProjectSaved} />
        </div>
    );
}

function Stat({ icon: Icon, label, value }) {
    return (
        <div className="card-steel flex items-center gap-4 p-5">
            <div className="border border-ink-line p-2.5">
                <Icon size={18} className="text-gold" />
            </div>
            <div>
                <div className="font-heading text-3xl font-black leading-none text-navy">
                    {value}
                </div>
                <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                    {label}
                </div>
            </div>
        </div>
    );
}

function SectionBlock({ title, count, children, emptyText, action }) {
    return (
        <div className="card-steel">
            <div className="flex items-center justify-between border-b border-ink-line px-5 py-3">
                <div className="flex items-center gap-2 font-heading text-lg font-bold uppercase tracking-wide text-navy">
                    {title}
                    <span className="font-mono text-[10px] text-ink-muted">{count}</span>
                </div>
                {action}
            </div>
            <div className="px-5 py-4">
                {count === 0 ? (
                    <div className="py-8 text-center text-sm text-ink-muted">
                        {emptyText}
                    </div>
                ) : (
                    children
                )}
            </div>
        </div>
    );
}

function StatusPill({ status, progress }) {
    const tone =
        status === "complete"
            ? "border-success text-success"
            : status === "failed"
              ? "border-destructive text-destructive"
              : "border-gold text-navy";
    const label =
        (status === "processing" || status === "queued") && progress?.total > 1
            ? `${status} ${progress.done}/${progress.total}`
            : status;
    return (
        <span className={`whitespace-nowrap border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${tone}`}>
            {label}
        </span>
    );
}
