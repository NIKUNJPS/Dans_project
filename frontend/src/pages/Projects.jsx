import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, FolderKanban, Pencil, Plus, Trash2, Users } from "lucide-react";
import { api, errMessage } from "@/lib/api";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { ProjectEditDialog, deleteProjectForever } from "@/components/projects/ProjectEditDialog";

export default function Projects() {
    const [projects, setProjects] = useState([]);
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(false);
    const [form, setForm] = useState({ name: "", description: "", tags: "" });
    const [saving, setSaving] = useState(false);
    const [editing, setEditing] = useState(null);
    const nav = useNavigate();
    const { user } = useAuth();
    const isOwner = (p) => user?.role === "super_admin" || p.owner_id === user?.id;

    const removeProject = async (p) => {
        if (await deleteProjectForever(p)) setProjects((list) => list.filter((x) => x.id !== p.id));
    };

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/api/projects");
            setProjects(data.items);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setLoading(false);
    };

    useEffect(() => {
        load();
    }, []);

    const create = async () => {
        setSaving(true);
        try {
            const { data } = await api.post("/api/projects", {
                name: form.name,
                description: form.description,
                tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
            });
            toast.success("Project created.");
            setOpen(false);
            setForm({ name: "", description: "", tags: "" });
            nav(`/projects/${data.id}`);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setSaving(false);
    };

    return (
        <div className="min-h-full bg-background">
            <div className="border-b border-ink-line bg-white px-10 py-10">
                <div className="flex items-end justify-between">
                    <div>
                        <div className="text-overline">Projects</div>
                        <h1 className="mt-2 font-heading text-5xl font-black leading-none tracking-tight text-navy">
                            All projects
                        </h1>
                    </div>
                    <Dialog open={open} onOpenChange={setOpen}>
                        <DialogTrigger asChild>
                            <button data-testid="new-project-btn" className="btn-gold">
                                <Plus size={14} />
                                New project
                            </button>
                        </DialogTrigger>
                        <DialogContent className="rounded-none border-ink-line sm:max-w-[500px]">
                            <DialogHeader>
                                <DialogTitle className="font-heading text-2xl font-black uppercase tracking-tight text-navy">
                                    Create project
                                </DialogTitle>
                                <DialogDescription>
                                    Organise your drawings, analyses, and RFIs.
                                </DialogDescription>
                            </DialogHeader>
                            <div className="space-y-3">
                                <div>
                                    <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                        Name
                                    </label>
                                    <Input
                                        data-testid="project-name-input"
                                        value={form.name}
                                        onChange={(e) =>
                                            setForm((f) => ({ ...f, name: e.target.value }))
                                        }
                                        className="h-11 rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0"
                                        placeholder="Northgate Resort · Tower C"
                                    />
                                </div>
                                <div>
                                    <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                        Description
                                    </label>
                                    <Textarea
                                        data-testid="project-description-input"
                                        value={form.description}
                                        onChange={(e) =>
                                            setForm((f) => ({
                                                ...f,
                                                description: e.target.value,
                                            }))
                                        }
                                        className="rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0"
                                        rows={3}
                                    />
                                </div>
                                <div>
                                    <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                        Tags (comma separated)
                                    </label>
                                    <Input
                                        data-testid="project-tags-input"
                                        value={form.tags}
                                        onChange={(e) =>
                                            setForm((f) => ({ ...f, tags: e.target.value }))
                                        }
                                        className="h-11 rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0"
                                        placeholder="resort, steel-frame, tower-c"
                                    />
                                </div>
                            </div>
                            <DialogFooter>
                                <button
                                    type="button"
                                    onClick={create}
                                    disabled={saving || !form.name}
                                    data-testid="create-project-btn"
                                    className="btn-gold w-full"
                                >
                                    {saving ? "Creating…" : "Create project"}
                                </button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>
                </div>
            </div>

            <div className="container-steel py-10">
                {loading ? (
                    <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                        {Array.from({ length: 6 }).map((_, i) => (
                            <Skeleton key={i} className="h-52 rounded-none" />
                        ))}
                    </div>
                ) : projects.length === 0 ? (
                    <div className="card-steel p-16 text-center">
                        <FolderKanban size={48} className="mx-auto text-ink-muted" />
                        <div className="mt-6 font-heading text-2xl font-bold uppercase tracking-tight text-navy">
                            No projects yet
                        </div>
                        <p className="mt-2 text-sm text-ink-muted">
                            Create your first project to start organising drawings and analyses.
                        </p>
                    </div>
                ) : (
                    <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                        {projects.map((p) => (
                            <div
                                key={p.id}
                                className="card-steel group flex h-full flex-col transition-all hover:border-navy hover:shadow-[0_8px_20px_-12px_rgba(13,34,64,0.4)]"
                            >
                            <Link
                                to={`/projects/${p.id}`}
                                data-testid={`project-card-${p.id}`}
                                className="flex flex-1 flex-col p-6 pb-3"
                            >
                                <div className="flex items-start justify-between">
                                    <div className="min-w-0 flex-1">
                                        <div className="font-heading text-xl font-bold uppercase tracking-tight text-navy line-clamp-2">
                                            {p.name}
                                        </div>
                                        <div className="mt-2 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                            {p.status}
                                        </div>
                                    </div>
                                    <ArrowRight
                                        size={18}
                                        className="text-ink-muted transition-transform group-hover:translate-x-1 group-hover:text-gold"
                                    />
                                </div>
                                <p className="mt-3 text-sm text-ink-muted line-clamp-2 min-h-[40px]">
                                    {p.description || "No description"}
                                </p>
                                <div className="mt-5 grid grid-cols-3 gap-2 border-t border-ink-line pt-4">
                                    <Stat label="Files" value={p.file_count} />
                                    <Stat label="Analyses" value={p.analysis_count} />
                                    <Stat label="RFIs" value={p.rfi_count} />
                                </div>
                                {p.tags?.length > 0 && (
                                    <div className="mt-4 flex flex-wrap gap-1">
                                        {p.tags.slice(0, 4).map((t) => (
                                            <span
                                                key={t}
                                                className="border border-ink-line bg-background px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider text-ink-muted"
                                            >
                                                {t}
                                            </span>
                                        ))}
                                    </div>
                                )}
                                <div className="mt-4 flex items-center gap-2 border-t border-ink-line pt-3 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                    <Users size={12} />
                                    {p.team_members?.length ?? 0} members
                                </div>
                            </Link>
                            {isOwner(p) && (
                            <div className="flex items-center justify-end gap-4 border-t border-ink-line px-6 py-2">
                                <button
                                    onClick={() => setEditing(p)}
                                    data-testid={`project-card-edit-${p.id}`}
                                    className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-navy hover:text-gold"
                                >
                                    <Pencil size={12} /> Edit
                                </button>
                                <button
                                    onClick={() => removeProject(p)}
                                    data-testid={`project-card-delete-${p.id}`}
                                    className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-ink-muted hover:text-destructive"
                                >
                                    <Trash2 size={12} /> Delete
                                </button>
                            </div>
                            )}
                            </div>
                        ))}
                    </div>
                )}
            </div>
            <ProjectEditDialog
                project={editing}
                open={!!editing}
                onOpenChange={(o) => !o && setEditing(null)}
                onSaved={(u) => setProjects((list) => list.map((x) => (x.id === u.id ? u : x)))}
            />
        </div>
    );
}

function Stat({ label, value }) {
    return (
        <div>
            <div className="font-heading text-2xl font-black leading-none text-navy">
                {value ?? 0}
            </div>
            <div className="mt-1 font-mono text-[9px] uppercase tracking-wider text-ink-muted">
                {label}
            </div>
        </div>
    );
}
