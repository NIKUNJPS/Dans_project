import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
    ArrowRight,
    Calendar,
    ChevronRight,
    FileIcon,
    FolderKanban,
    MessageSquareQuote,
    Plus,
    Sparkles,
    Users,
} from "lucide-react";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";

export default function ProjectDetail() {
    const { id } = useParams();
    const nav = useNavigate();
    const [project, setProject] = useState(null);
    const [files, setFiles] = useState([]);
    const [analyses, setAnalyses] = useState([]);
    const [rfis, setRfis] = useState([]);
    const [loading, setLoading] = useState(true);

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
                    <div className="flex gap-3">
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
                        {files.slice(0, 6).map((f) => (
                            <div
                                key={f.id}
                                data-testid={`project-file-${f.id}`}
                                className="flex items-center justify-between border-b border-ink-line py-2 last:border-b-0"
                            >
                                <div className="min-w-0 flex-1 truncate text-sm text-navy">
                                    {f.original_name}
                                </div>
                                <div className="font-mono text-[10px] uppercase text-ink-muted">
                                    {f.size_mb} MB
                                </div>
                            </div>
                        ))}
                    </SectionBlock>

                    <SectionBlock
                        title="Analyses"
                        count={analyses.length}
                        emptyText="No analyses yet."
                    >
                        {analyses.slice(0, 6).map((a) => (
                            <Link
                                key={a.id}
                                to={`/analyses/${a.id}`}
                                data-testid={`project-analysis-${a.id}`}
                                className="flex items-center justify-between border-b border-ink-line py-2 last:border-b-0 hover:text-gold"
                            >
                                <div className="min-w-0 flex-1 truncate font-heading text-sm font-semibold uppercase tracking-wide text-navy hover:text-gold">
                                    {a.mode_label || a.mode}
                                </div>
                                <div className="font-mono text-[10px] uppercase text-ink-muted">
                                    {a.status}
                                </div>
                            </Link>
                        ))}
                    </SectionBlock>
                </div>
            </div>
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
