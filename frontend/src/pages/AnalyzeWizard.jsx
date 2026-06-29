import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useDropzone } from "react-dropzone";
import {
    CheckCircle2,
    CloudUpload,
    FileIcon,
    Sparkles,
    Trash2,
} from "lucide-react";
import { api, errMessage } from "@/lib/api";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";

export default function AnalyzeWizard() {
    const { id: projectId } = useParams();
    const nav = useNavigate();

    const [files, setFiles] = useState([]);
    const [uploading, setUploading] = useState(false);
    const [modes, setModes] = useState([]);
    const [groups, setGroups] = useState([]);
    const [selectedMode, setSelectedMode] = useState(null);
    const [instructions, setInstructions] = useState("");
    const [running, setRunning] = useState(false);
    const [loadingModes, setLoadingModes] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/api/analyses/modes");
                setModes(data.modes || []);
                setGroups(data.groups || []);
                if (data.modes?.length) setSelectedMode(data.modes[0].id);
            } catch (e) {
                toast.error(errMessage(e));
            }
            setLoadingModes(false);
        })();
    }, []);

    useEffect(() => {
        if (!projectId) return;
        (async () => {
            try {
                const { data } = await api.get(`/api/files?project_id=${projectId}`);
                setFiles(data.items);
            } catch {}
        })();
    }, [projectId]);

    const onDrop = useCallback(
        async (accepted) => {
            if (!accepted.length) return;
            setUploading(true);
            for (const f of accepted) {
                const fd = new FormData();
                fd.append("file", f);
                if (projectId) fd.append("project_id", projectId);
                try {
                    const { data } = await api.post("/api/files/upload", fd, {
                        headers: { "Content-Type": "multipart/form-data" },
                    });
                    setFiles((prev) => [data, ...prev]);
                } catch (e) {
                    toast.error(`${f.name} — ${errMessage(e)}`);
                }
            }
            setUploading(false);
        },
        [projectId],
    );

    const { getRootProps, getInputProps, isDragActive } = useDropzone({
        onDrop,
        multiple: true,
        maxSize: 500 * 1024 * 1024,
    });

    const removeFile = async (fid) => {
        try {
            await api.delete(`/api/files/${fid}`);
            setFiles((prev) => prev.filter((f) => f.id !== fid));
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const runAnalysis = async () => {
        if (!selectedMode) {
            toast.error("Pick a mode first");
            return;
        }
        setRunning(true);
        try {
            const { data } = await api.post("/api/analyses", {
                project_id: projectId || null,
                file_ids: files.map((f) => f.id),
                mode: selectedMode,
                input_text: instructions,
            });
            toast.success("Analysis queued — redirecting…");
            nav(`/analyses/${data.id}`);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setRunning(false);
    };

    const grouped = useMemo(() => {
        const out = {};
        for (const g of groups) out[g] = [];
        for (const m of modes) {
            (out[m.group] ||= []).push(m);
        }
        return out;
    }, [modes, groups]);

    const pickedMode = modes.find((m) => m.id === selectedMode);

    return (
        <div className="min-h-full bg-background">
            <div className="border-b border-ink-line bg-white px-10 py-10">
                <div className="text-overline">Analysis wizard</div>
                <h1 className="mt-2 font-heading text-5xl font-black leading-none tracking-tight text-navy">
                    Submit design package
                </h1>
                <p className="mt-3 max-w-3xl text-ink-muted">
                    Drop drawings, models & specs. Pick a mode enabled by your administrator.
                    STRUCTMIND CORE generates role-specific deliverables.
                </p>
            </div>

            <div className="container-steel grid gap-8 py-10 lg:grid-cols-5">
                {/* LEFT — Upload */}
                <div className="lg:col-span-3">
                    <section className="card-steel p-6">
                        <div className="flex items-center justify-between">
                            <div className="font-heading text-xl font-bold uppercase tracking-tight text-navy">
                                01 · Upload
                            </div>
                            <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                {files.length} file{files.length === 1 ? "" : "s"}
                            </div>
                        </div>
                        <div
                            {...getRootProps()}
                            data-testid="file-dropzone"
                            className={`mt-5 cursor-pointer border-2 border-dashed p-12 text-center transition-colors ${
                                isDragActive
                                    ? "border-gold bg-gold-pale"
                                    : "border-ink-line bg-background hover:border-navy hover:bg-gold-pale/40"
                            }`}
                        >
                            <input {...getInputProps()} data-testid="file-input" />
                            <CloudUpload
                                size={48}
                                className="mx-auto text-navy"
                                strokeWidth={1.25}
                            />
                            <div className="mt-4 font-heading text-2xl font-black uppercase tracking-tight text-navy">
                                Drop drawings, models & specs
                            </div>
                            <div className="mt-2 font-mono text-[11px] uppercase tracking-wider text-ink-muted">
                                PDF · DWG · DXF · IFC · RVT · NWD · NC1 · DSTV · XLSX
                            </div>
                            <div className="mt-4 inline-flex items-center gap-2 border border-navy bg-white px-4 py-2 font-heading text-sm font-bold uppercase tracking-wider text-navy">
                                Browse files
                            </div>
                        </div>

                        <div className="mt-6 space-y-2">
                            {uploading && (
                                <div className="flex items-center gap-2 border border-gold bg-gold-pale px-3 py-2 font-mono text-xs uppercase tracking-wider text-navy">
                                    <span className="h-2 w-2 animate-pulse-ring rounded-full bg-gold" />
                                    Uploading…
                                </div>
                            )}
                            {files.map((f) => (
                                <div
                                    key={f.id}
                                    data-testid={`uploaded-file-${f.id}`}
                                    className="flex items-center justify-between border border-ink-line bg-white px-4 py-3"
                                >
                                    <div className="flex min-w-0 items-center gap-3">
                                        <FileIcon size={16} className="text-navy" />
                                        <div className="min-w-0">
                                            <div className="truncate font-heading text-sm font-semibold uppercase text-navy">
                                                {f.original_name}
                                            </div>
                                            <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                                {f.size_mb} MB · SHA-256 {f.blockchain_hash?.slice(0, 12)}…
                                            </div>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-3">
                                        <span className="inline-flex items-center gap-1 border border-success px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-success">
                                            <CheckCircle2 size={10} />
                                            Ready
                                        </span>
                                        <button
                                            onClick={() => removeFile(f.id)}
                                            className="text-ink-muted hover:text-destructive"
                                            aria-label="Remove"
                                        >
                                            <Trash2 size={14} />
                                        </button>
                                    </div>
                                </div>
                            ))}
                        </div>

                        <div className="mt-6">
                            <label className="mb-2 block text-overline">
                                Additional instructions (optional)
                            </label>
                            <Textarea
                                data-testid="analysis-instructions"
                                value={instructions}
                                onChange={(e) => setInstructions(e.target.value)}
                                rows={3}
                                className="rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0"
                                placeholder="Focus on steel-to-concrete anchorage, verify AISC 341 for seismic category D, flag any moment connection gaps."
                            />
                        </div>
                    </section>
                </div>

                {/* RIGHT — Mode */}
                <div className="lg:col-span-2">
                    <section className="card-steel sticky top-6 p-6">
                        <div className="flex items-center justify-between">
                            <div className="font-heading text-xl font-bold uppercase tracking-tight text-navy">
                                02 · Mode
                            </div>
                            <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                {modes.length} mode{modes.length === 1 ? "" : "s"} enabled
                            </div>
                        </div>
                        {loadingModes ? (
                            <Skeleton className="mt-4 h-64 rounded-none" />
                        ) : modes.length === 0 ? (
                            <div
                                data-testid="no-modes-enabled"
                                className="mt-6 border border-dashed border-ink-line bg-background p-6 text-center"
                            >
                                <div className="font-heading text-base font-bold uppercase text-navy">
                                    No analysis modes enabled
                                </div>
                                <div className="mt-2 text-sm text-ink-muted">
                                    Your super admin has not yet enabled any analysis modes for your account.
                                    Contact them to unlock the modes you need.
                                </div>
                            </div>
                        ) : (
                            <div className="mt-5 max-h-[520px] space-y-5 overflow-y-auto pr-1">
                                {groups.map((g) => (
                                    (grouped[g] || []).length > 0 && (
                                        <div key={g}>
                                            <div className="sticky top-0 z-10 bg-white pb-1 font-mono text-[9px] font-bold uppercase tracking-[0.22em] text-gold">
                                                {g}
                                            </div>
                                            <div className="space-y-2 pt-1">
                                                {(grouped[g] || []).map((m) => (
                                                    <button
                                                        key={m.id}
                                                        type="button"
                                                        onClick={() => setSelectedMode(m.id)}
                                                        data-testid={`mode-${m.id}`}
                                                        className={`group flex w-full items-start gap-2 border p-3 text-left transition-all ${
                                                            selectedMode === m.id
                                                                ? "border-gold bg-gold-pale"
                                                                : "border-ink-line bg-white hover:border-navy"
                                                        }`}
                                                    >
                                                        <div className="flex-1">
                                                            <div className="font-heading text-sm font-bold uppercase tracking-tight text-navy">
                                                                {m.label}
                                                            </div>
                                                            <div className="mt-1 line-clamp-2 text-xs text-ink-muted">
                                                                {m.description}
                                                            </div>
                                                            <div className="mt-1 font-mono text-[9px] uppercase tracking-wider text-ink-muted">
                                                                {m.time}
                                                            </div>
                                                        </div>
                                                    </button>
                                                ))}
                                            </div>
                                        </div>
                                    )
                                ))}
                            </div>
                        )}

                        <button
                            type="button"
                            onClick={runAnalysis}
                            disabled={running || !selectedMode || modes.length === 0}
                            data-testid="run-analysis-btn"
                            className="btn-gold mt-6 w-full"
                        >
                            {running ? "Queuing…" : "Run analysis"}
                            <Sparkles size={14} />
                        </button>
                        {pickedMode && (
                            <div className="mt-3 text-center font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                Next up: {pickedMode.label} · est. {pickedMode.time}
                            </div>
                        )}
                    </section>
                </div>
            </div>
        </div>
    );
}
