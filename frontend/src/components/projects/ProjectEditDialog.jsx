import { useEffect, useState } from "react";
import { api, errMessage } from "@/lib/api";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

const FIELD = "rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0";
const LABEL = "mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted";

/** Rename a project and edit its description and tags. Calls onSaved(updatedProject). */
export function ProjectEditDialog({ project, open, onOpenChange, onSaved }) {
    const [form, setForm] = useState({ name: "", description: "", tags: "" });
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (open && project) {
            setForm({
                name: project.name || "",
                description: project.description || "",
                tags: (project.tags || []).join(", "),
            });
        }
    }, [open, project]);

    const save = async () => {
        setSaving(true);
        try {
            const { data } = await api.put(`/api/projects/${project.id}`, {
                name: form.name.trim(),
                description: form.description,
                tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
            });
            toast.success("Project updated");
            onSaved?.(data);
            onOpenChange(false);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setSaving(false);
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="rounded-none border-ink-line sm:max-w-[500px]">
                <DialogHeader>
                    <DialogTitle className="font-heading text-2xl font-black uppercase tracking-tight text-navy">
                        Edit project
                    </DialogTitle>
                    <DialogDescription>Rename the project or update its description and tags.</DialogDescription>
                </DialogHeader>
                <form
                    className="space-y-3"
                    onSubmit={(e) => {
                        e.preventDefault();
                        if (form.name.trim()) save();
                    }}
                >
                    <div>
                        <label className={LABEL}>Name</label>
                        <Input
                            data-testid="project-edit-name"
                            value={form.name}
                            maxLength={128}
                            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                            className={`h-11 ${FIELD}`}
                        />
                    </div>
                    <div>
                        <label className={LABEL}>Description</label>
                        <Textarea
                            data-testid="project-edit-description"
                            value={form.description}
                            maxLength={2000}
                            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                            className={FIELD}
                            rows={3}
                        />
                    </div>
                    <div>
                        <label className={LABEL}>Tags (comma separated)</label>
                        <Input
                            data-testid="project-edit-tags"
                            value={form.tags}
                            onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))}
                            className={`h-11 ${FIELD}`}
                        />
                    </div>
                    <DialogFooter>
                        <button
                            type="submit"
                            disabled={saving || !form.name.trim()}
                            data-testid="project-edit-save"
                            className="btn-gold w-full"
                        >
                            {saving ? "Saving…" : "Save changes"}
                        </button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}

/** Permanently delete a project after confirmation. Resolves true when deleted. */
export async function deleteProjectForever(project) {
    if (
        !window.confirm(
            `Permanently delete "${project.name}" with all its drawings, reports, RFIs and estimates? This cannot be undone.`,
        )
    )
        return false;
    try {
        await api.delete(`/api/projects/${project.id}?hard=true`);
        toast.success("Project deleted");
        return true;
    } catch (e) {
        toast.error(errMessage(e));
        return false;
    }
}
