import { useCallback, useEffect, useState } from "react";
import { useDropzone } from "react-dropzone";
import { toast } from "sonner";
import {
    Calculator,
    Download,
    FileText,
    History,
    Loader2,
    Printer,
    ShieldCheck,
    Trash2,
    UploadCloud,
} from "lucide-react";
import { api, downloadFile, errMessage, fileBase } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { usePermissions } from "@/hooks/usePermissions";

// ── helpers ────────────────────────────────────────────────────────────────
const CONF_COLOR = {
    High: "bg-green-100 text-green-800 border-green-300",
    Medium: "bg-amber-100 text-amber-800 border-amber-300",
    Low: "bg-red-100 text-red-800 border-red-300",
};

function Kpi({ label, value, sub }) {
    return (
        <div className="border border-ink-line bg-white p-4">
            <div className="border-t-2 border-gold pt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                {label}
            </div>
            <div className="mt-1 font-heading text-2xl font-bold text-navy">{value}</div>
            {sub && <div className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-ink-muted">{sub}</div>}
        </div>
    );
}

function SectionTitle({ children }) {
    return (
        <h2 className="mb-3 mt-8 font-heading text-sm font-bold uppercase tracking-[0.2em] text-navy">
            {children}
        </h2>
    );
}

export default function Estimation() {
    const { user } = useAuth();
    const { can, countryAllowed, isSuperAdmin } = usePermissions();

    const [role, setRole] = useState(user?.role === "fabricator" ? "fabricator" : user?.role === "detailer" ? "detailer" : "fabricator");
    const [country, setCountry] = useState(user?.country || "USA");
    const [countries, setCountries] = useState([]);
    const [files, setFiles] = useState([]);
    const [uploading, setUploading] = useState(false);
    const [rateLow, setRateLow] = useState("");
    const [rateHigh, setRateHigh] = useState("");
    const [projectName, setProjectName] = useState("");
    const [running, setRunning] = useState(false);
    const [result, setResult] = useState(null);
    const [downloading, setDownloading] = useState(false);
    const [history, setHistory] = useState([]);

    const loadHistory = useCallback(async () => {
        try {
            const { data } = await api.get("/api/estimation/");
            setHistory(data.items || []);
        } catch {}
    }, []);

    useEffect(() => {
        loadHistory();
    }, [loadHistory]);

    // The take-off runs as a background job; poll until it finishes.
    useEffect(() => {
        if (!result?.id || result.status !== "processing") return;
        const t = setInterval(async () => {
            try {
                const { data } = await api.get(`/api/estimation/${result.id}`);
                if (data.status === "complete") {
                    setResult(data);
                    setRunning(false);
                    toast.success("Take-off complete.");
                    loadHistory();
                } else if (data.status === "failed") {
                    setResult(null);
                    setRunning(false);
                    toast.error(data.error_message || "Estimate failed.");
                    loadHistory();
                } else {
                    setResult(data);
                }
            } catch {}
        }, 3000);
        return () => clearInterval(t);
    }, [result?.id, result?.status, loadHistory]);

    const openEstimate = async (eid) => {
        try {
            const { data } = await api.get(`/api/estimation/${eid}`);
            setResult(data);
            if (data.role) setRole(data.role);
            window.scrollTo({ top: 0, behavior: "smooth" });
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const deleteEstimate = async (eid) => {
        if (!window.confirm("Delete this estimate from your history? This cannot be undone.")) return;
        try {
            await api.delete(`/api/estimation/${eid}`);
            setHistory((prev) => prev.filter((e) => e.id !== eid));
            if (result?.id === eid) setResult(null);
            toast.success("Estimate deleted");
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const isFab = role === "fabricator";
    const rateUnit = isFab ? "/ ton" : "/ hr";

    // Default rate band per role (matches the backend schema defaults).
    useEffect(() => {
        if (isFab) {
            setRateLow((p) => p || "2400");
            setRateHigh((p) => p || "3600");
        } else {
            setRateLow((p) => p || "18");
            setRateHigh((p) => p || "25");
        }
    }, [role]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/api/estimation/countries");
                setCountries(data.countries || []);
                if (data.countries?.length && !data.countries.find((c) => c.code === country)) {
                    setCountry(data.countries[0].code);
                }
            } catch {
                setCountries([{ code: "USA", name: "United States", currency: "USD" }]);
            }
        })();
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    const onDrop = useCallback(async (accepted) => {
        if (!accepted.length) return;
        setUploading(true);
        for (const f of accepted) {
            const fd = new FormData();
            fd.append("file", f);
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
    }, []);

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

    const canRun = can("canRunEstimation") || isSuperAdmin;

    const calculate = async () => {
        if (!files.length) {
            toast.error("Upload at least one drawing first.");
            return;
        }
        if (Number(rateLow) <= 0 || Number(rateHigh) <= 0) {
            toast.error("Enter both LOW and HIGH rates.");
            return;
        }
        setRunning(true);
        setResult(null);
        try {
            const { data } = await api.post("/api/estimation/ai-calculate", {
                role,
                country,
                rate_low: Number(rateLow),
                rate_high: Number(rateHigh),
                file_ids: files.map((f) => f.id),
                project_name: projectName || undefined,
            });
            setResult(data);
            if (data.status === "processing") {
                toast.success("Reading the drawing set — the estimate appears here when it is ready.");
                loadHistory();
                return; // polling finishes the run
            }
            toast.success("Take-off complete.");
        } catch (e) {
            toast.error(errMessage(e));
        }
        setRunning(false);
    };

    const downloadPdf = async () => {
        if (!result?.id) return;
        setDownloading(true);
        try {
            const roleLabel = role.charAt(0).toUpperCase() + role.slice(1);
            await downloadFile(
                `/api/estimation/${result.id}/pdf`,
                `${fileBase(result.project_name || "STRUCTMIND", roleLabel, "Estimate")}.pdf`,
            );
        } catch (e) {
            toast.error(errMessage(e));
        }
        setDownloading(false);
    };

    const processing = result?.status === "processing";
    const v = result?.result?.visible;
    const conf = v?.confidence;
    const lineItems = v?.line_items || [];
    const cats = v?.category_summary || [];
    const buildup = v?.cost_buildup || [];
    const rfis = v?.open_rfis || [];
    const assumptions = v?.assumptions || [];
    const ex = v?.extracted || {};

    const netKg = cats.reduce((s, c) => s + (c.weight_kg || 0), 0);
    const tonUnit = ex.ton_unit || "tonne";

    return (
        <div className="mx-auto max-w-6xl px-8 py-8">
            <div className="flex items-center gap-3">
                <Calculator className="text-gold" size={26} />
                <div>
                    <h1 className="font-heading text-2xl font-bold uppercase tracking-wide text-navy">
                        AI Estimation
                    </h1>
                    <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-ink-muted">
                        Upload drawings · STRUCTMIND CORE builds a member-by-member take-off
                    </p>
                </div>
            </div>

            {!canRun && (
                <div className="no-print mt-6 border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800">
                    Estimation is not enabled for your account. Ask a super admin to enable
                    <span className="font-semibold"> canRunEstimation</span>.
                </div>
            )}

            {/* ── INPUT PANEL ── */}
            <div className="no-print mt-6 grid gap-6 md:grid-cols-2">
                <div className="space-y-4">
                    {isSuperAdmin && (
                        <div>
                            <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">Role</label>
                            <select
                                value={role}
                                onChange={(e) => setRole(e.target.value)}
                                className="mt-1 w-full rounded-none border border-ink-line bg-white px-3 py-2 font-mono text-sm uppercase text-navy focus:border-gold focus:outline-none"
                            >
                                <option value="fabricator">Fabricator (tonnage × per-ton)</option>
                                <option value="detailer">Detailer (hours × per-hour)</option>
                            </select>
                        </div>
                    )}

                    <div>
                        <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">Country</label>
                        <select
                            value={country}
                            onChange={(e) => setCountry(e.target.value)}
                            className="mt-1 w-full rounded-none border border-ink-line bg-white px-3 py-2 font-mono text-sm uppercase text-navy focus:border-gold focus:outline-none"
                        >
                            {countries.map((c) => (
                                <option key={c.code} value={c.code} disabled={!countryAllowed(c.code)}>
                                    {c.name || c.code} {c.currency ? `· ${c.currency}` : ""}
                                </option>
                            ))}
                        </select>
                    </div>

                    <div>
                        <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">Project name (optional)</label>
                        <input
                            value={projectName}
                            onChange={(e) => setProjectName(e.target.value)}
                            placeholder="e.g. Warehouse Phase 2"
                            className="mt-1 w-full rounded-none border border-ink-line bg-white px-3 py-2 text-sm text-navy focus:border-gold focus:outline-none"
                        />
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">Rate LOW ({rateUnit})</label>
                            <input
                                type="number"
                                value={rateLow}
                                onChange={(e) => setRateLow(e.target.value)}
                                className="mt-1 w-full rounded-none border border-ink-line bg-white px-3 py-2 text-sm text-navy focus:border-gold focus:outline-none"
                            />
                        </div>
                        <div>
                            <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">Rate HIGH ({rateUnit})</label>
                            <input
                                type="number"
                                value={rateHigh}
                                onChange={(e) => setRateHigh(e.target.value)}
                                className="mt-1 w-full rounded-none border border-ink-line bg-white px-3 py-2 text-sm text-navy focus:border-gold focus:outline-none"
                            />
                        </div>
                    </div>
                </div>

                {/* Upload */}
                <div>
                    <div
                        {...getRootProps()}
                        className={`flex h-[132px] cursor-pointer flex-col items-center justify-center border-2 border-dashed p-4 text-center transition ${
                            isDragActive ? "border-gold bg-gold-pale/30" : "border-ink-line bg-white hover:border-navy"
                        }`}
                    >
                        <input {...getInputProps()} />
                        <UploadCloud className="text-navy" size={26} />
                        <div className="mt-2 font-heading text-sm uppercase tracking-wide text-navy">
                            {uploading ? "Uploading…" : "Drop drawings / BOM / schedules"}
                        </div>
                        <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                            PDF · PNG · JPG · XLSX · CSV · NC1
                        </div>
                    </div>

                    <div className="mt-3 max-h-[150px] space-y-1 overflow-y-auto">
                        {files.map((f) => (
                            <div key={f.id} className="flex items-center justify-between border border-ink-line bg-white px-3 py-1.5">
                                <div className="flex min-w-0 items-center gap-2">
                                    <FileText size={14} className="text-ink-muted" />
                                    <span className="truncate text-xs text-navy">{f.original_name}</span>
                                </div>
                                <button onClick={() => removeFile(f.id)} className="text-ink-muted hover:text-red-600">
                                    <Trash2 size={14} />
                                </button>
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            <button
                onClick={calculate}
                disabled={running || !canRun || !files.length}
                className="no-print mt-6 inline-flex items-center gap-2 bg-navy px-6 py-3 font-heading text-sm uppercase tracking-wider text-white transition hover:bg-navy-mid disabled:cursor-not-allowed disabled:opacity-40"
            >
                {running ? <Loader2 className="animate-spin" size={16} /> : <Calculator size={16} />}
                {running ? "STRUCTMIND CORE is analysing…" : "Run take-off & estimate"}
            </button>

            {processing && (
                <div className="no-print mt-8 border border-gold bg-gold-pale/30 p-5">
                    <div className="flex items-center gap-2 font-heading text-sm uppercase tracking-wide text-navy">
                        <Loader2 className="animate-spin" size={16} />
                        {result.progress?.label || "Preparing the drawing set"}
                        {result.progress?.total > 1 ? ` — batch ${Math.min(result.progress.done + 1, result.progress.total)} of ${result.progress.total}` : ""}
                    </div>
                    {result.progress?.total > 0 && (
                        <div className="mt-3 h-2 w-full border border-ink-line bg-white">
                            <div
                                className="h-full bg-gold transition-all duration-700"
                                style={{ width: `${Math.max(4, Math.round((100 * (result.progress.done || 0)) / result.progress.total))}%` }}
                            />
                        </div>
                    )}
                    <div className="mt-2 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                        Large binders are read in page batches and consolidated into one take-off. You can leave this page — the estimate is saved to your history.
                    </div>
                </div>
            )}

            {/* ── RESULT ── */}
            {v && (
                <div className="mt-10 border-t border-ink-line pt-6">
                    <div className="flex items-center justify-between">
                        <h2 className="font-heading text-lg font-bold uppercase tracking-wide text-navy">
                            {isFab ? "Fabrication Estimate" : "Detailing Estimate"}
                            {result?.project_name ? ` — ${result.project_name}` : ""}
                        </h2>
                        <div className="no-print flex gap-2">
                            <button
                                onClick={() => window.print()}
                                className="inline-flex items-center gap-2 border border-navy px-4 py-2 font-heading text-xs uppercase tracking-wider text-navy hover:bg-navy hover:text-white"
                            >
                                <Printer size={14} />
                                Print
                            </button>
                            <button
                                onClick={downloadPdf}
                                disabled={downloading}
                                className="inline-flex items-center gap-2 border border-navy px-4 py-2 font-heading text-xs uppercase tracking-wider text-navy hover:bg-navy hover:text-white disabled:opacity-40"
                            >
                                {downloading ? <Loader2 className="animate-spin" size={14} /> : <Download size={14} />}
                                PDF
                            </button>
                        </div>
                    </div>

                    <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                        {isFab ? (
                            <Kpi
                                label="Estimated Tonnage"
                                value={`${(ex.tonnage ?? 0).toLocaleString("en-US")} ${tonUnit === "short ton" ? "tons" : "t"}`}
                                sub={`${rfis.length.toLocaleString("en-US")} RFIs`}
                            />
                        ) : (
                            <Kpi label="Detailing hours" value={(v.total_hours ?? 0).toLocaleString()} sub={`${ex.drawings ?? 0} drawings`} />
                        )}
                        <Kpi label="Rate band" value={`${v.user_rate_low} → ${v.user_rate_high}`} sub={isFab ? `per ${tonUnit}` : rateUnit} />
                        <Kpi label="Estimate (mid)" value={v.grand_mid} sub={v.grand_range_text} />
                        {conf && (
                            <div className="border border-ink-line bg-white p-4">
                                <div className="border-t-2 border-gold pt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                    Confidence
                                </div>
                                <div className="mt-1 flex items-center gap-2">
                                    <span className="font-heading text-2xl font-bold text-navy">{conf.score}</span>
                                    <span className={`border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${CONF_COLOR[conf.label] || "bg-ink-line/30 text-ink-muted"}`}>
                                        {conf.label}
                                    </span>
                                </div>
                                <div className="mt-1 text-[11px] leading-tight text-ink-muted">{conf.note}</div>
                            </div>
                        )}
                    </div>

                    {/* Category summary */}
                    {cats.length > 0 && (
                        <>
                            <SectionTitle>Take-off summary by category</SectionTitle>
                            <div className="overflow-x-auto border border-ink-line">
                                <table className="w-full text-sm">
                                    <thead className="bg-navy text-white">
                                        <tr>
                                            {["Category", "Members (Σ Qty)", "Length (m)", "Weight (kg)", "Metric t"].map((h) => (
                                                <th key={h} className="px-3 py-2 text-left font-heading text-[11px] uppercase tracking-wider">{h}</th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {cats.map((c, i) => (
                                            <tr key={i} className={i % 2 ? "bg-background" : "bg-white"}>
                                                <td className="px-3 py-1.5 text-navy">{c.category}</td>
                                                <td className="px-3 py-1.5">{c.count?.toLocaleString()}</td>
                                                <td className="px-3 py-1.5">{c.length_m?.toLocaleString()}</td>
                                                <td className="px-3 py-1.5">{c.weight_kg?.toLocaleString()}</td>
                                                <td className="px-3 py-1.5 font-semibold">{c.tons?.toLocaleString()}</td>
                                            </tr>
                                        ))}
                                        <tr className="border-t-2 border-ink-line bg-white font-semibold">
                                            <td className="px-3 py-1.5 text-navy">Net member weight</td>
                                            <td className="px-3 py-1.5">{cats.reduce((s, c) => s + (c.count || 0), 0).toLocaleString()}</td>
                                            <td />
                                            <td className="px-3 py-1.5">{Math.round(netKg).toLocaleString()}</td>
                                            <td className="px-3 py-1.5">{(netKg / 1000).toFixed(3)}</td>
                                        </tr>
                                        {(ex.allowances || []).map((a, i) => (
                                            <tr key={`al-${i}`} className="bg-background text-ink-muted">
                                                <td className="px-3 py-1.5">+ {a.label} ({a.pct}%)</td>
                                                <td colSpan={2} />
                                                <td className="px-3 py-1.5">{Math.round(a.kg).toLocaleString()}</td>
                                                <td className="px-3 py-1.5">{(a.kg / 1000).toFixed(3)}</td>
                                            </tr>
                                        ))}
                                        <tr className="border-t-2 border-gold bg-gold-pale/40 font-bold">
                                            <td className="px-3 py-2 text-navy">ESTIMATED TONNAGE</td>
                                            <td colSpan={2} className="px-3 py-2 text-navy">
                                                {ex.tonnage_short !== undefined ? `${Number(ex.tonnage_short).toFixed(2)} tons (2,000 lb basis)` : ""}
                                            </td>
                                            <td className="px-3 py-2 text-navy">{Math.round(ex.gross_kg || netKg).toLocaleString()}</td>
                                            <td className="px-3 py-2 text-navy">{Number(ex.tonnage_t ?? netKg / 1000).toFixed(3)}</td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}

                    {/* Cost build-up */}
                    {buildup.length > 0 && (
                        <>
                            <SectionTitle>Cost build-up (mid scenario)</SectionTitle>
                            <div className="overflow-x-auto border border-ink-line">
                                <table className="w-full text-sm">
                                    <thead className="bg-navy text-white">
                                        <tr>
                                            {["Component", "Share", "Amount"].map((h) => (
                                                <th key={h} className="px-3 py-2 text-left font-heading text-[11px] uppercase tracking-wider">{h}</th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {buildup.map((b, i) => (
                                            <tr key={i} className={i % 2 ? "bg-background" : "bg-white"}>
                                                <td className="px-3 py-1.5 text-navy">{b.item}</td>
                                                <td className="px-3 py-1.5">{b.share}</td>
                                                <td className="px-3 py-1.5">{b.amount}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}

                    {/* Itemized line items (fabricator) */}
                    {isFab && lineItems.length > 0 && (
                        <>
                            <SectionTitle>Itemized material take-off ({lineItems.length} line items)</SectionTitle>
                            <div className="print-expand max-h-[420px] overflow-auto border border-ink-line">
                                <table className="w-full text-xs">
                                    <thead className="sticky top-0 bg-navy text-white">
                                        <tr>
                                            {["#", "Type", "Mark", "Profile", "Qty", "Len (mm)", "Wt (kg)", "Grade", "Sheet", "Flag"].map((h) => (
                                                <th key={h} className="px-2 py-2 text-left font-heading uppercase tracking-wider">{h}</th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {lineItems.map((it, i) => (
                                            <tr key={i} className={`${i % 2 ? "bg-background" : "bg-white"} ${it.flag ? "text-amber-700" : ""}`}>
                                                <td className="px-2 py-1">{it.no}</td>
                                                <td className="px-2 py-1">{it.type}</td>
                                                <td className="px-2 py-1">{it.mark}</td>
                                                <td className="px-2 py-1">{it.profile}</td>
                                                <td className="px-2 py-1">{it.qty}</td>
                                                <td className="px-2 py-1">{Number(it.length_mm || 0).toLocaleString()}</td>
                                                <td className="px-2 py-1">{Number(it.weight_kg || 0).toLocaleString()}</td>
                                                <td className="px-2 py-1">{it.grade}</td>
                                                <td className="px-2 py-1">{it.source_sheet}</td>
                                                <td className="px-2 py-1">{it.flag}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}

                    {/* Detailer workload */}
                    {!isFab && lineItems.length > 0 && (
                        <>
                            <SectionTitle>Detailing workload breakdown</SectionTitle>
                            <div className="overflow-x-auto border border-ink-line">
                                <table className="w-full text-sm">
                                    <thead className="bg-navy text-white">
                                        <tr>
                                            {["#", "Task", "Qty", "Unit", "Hrs/unit", "Hours", "Basis"].map((h) => (
                                                <th key={h} className="px-3 py-2 text-left font-heading text-[11px] uppercase tracking-wider">{h}</th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {lineItems.map((it, i) => (
                                            <tr key={i} className={i % 2 ? "bg-background" : "bg-white"}>
                                                <td className="px-3 py-1.5">{it.no}</td>
                                                <td className="px-3 py-1.5 text-navy">{it.task}</td>
                                                <td className="px-3 py-1.5">{it.qty}</td>
                                                <td className="px-3 py-1.5">{it.unit}</td>
                                                <td className="px-3 py-1.5">{it.hours_per_unit}</td>
                                                <td className="px-3 py-1.5 font-semibold">{it.hours}</td>
                                                <td className="px-3 py-1.5 text-ink-muted">{it.basis}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}

                    {/* Assumptions & RFIs */}
                    {(assumptions.length > 0 || rfis.length > 0) && (
                        <div className="mt-8 grid gap-6 md:grid-cols-2">
                            {assumptions.length > 0 && (
                                <div>
                                    <SectionTitle>Assumptions</SectionTitle>
                                    <ul className="space-y-1.5">
                                        {assumptions.map((a, i) => (
                                            <li key={i} className="flex gap-2 text-sm text-ink">
                                                <span className="text-gold">▸</span>
                                                <span>{a}</span>
                                            </li>
                                        ))}
                                    </ul>
                                </div>
                            )}
                            {rfis.length > 0 && (
                                <div>
                                    <SectionTitle>Open RFIs / gaps</SectionTitle>
                                    <div className="space-y-2">
                                        {rfis.map((r, i) => (
                                            <div key={i} className="border border-ink-line bg-white p-3">
                                                <div className="flex items-center gap-2">
                                                    <ShieldCheck size={13} className="text-gold" />
                                                    <span className="font-mono text-[11px] font-semibold text-navy">{r.id}</span>
                                                    <span className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">{r.priority}</span>
                                                </div>
                                                <div className="mt-1 text-sm text-ink">{r.question}</div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    )}

                    {ex.excluded_scope?.length > 0 && (
                        <p className="mt-6 text-sm text-ink">
                            <span className="font-semibold text-navy">Excluded: </span>
                            {ex.excluded_scope.join("; ")}
                        </p>
                    )}
                    {ex.notes && (
                        <p className="mt-6 font-mono text-[11px] uppercase tracking-wider text-ink-muted">
                            Basis: {ex.notes} · Engine: {result?.engine || "STRUCTMIND CORE"}
                        </p>
                    )}
                </div>
            )}

            {/* ── HISTORY ── */}
            <div className="no-print mt-12 border-t border-ink-line pt-6">
                <h2 className="flex items-center gap-2 font-heading text-sm font-bold uppercase tracking-[0.2em] text-navy">
                    <History size={14} className="text-gold" />
                    Estimate history
                </h2>
                {history.length === 0 ? (
                    <p className="mt-3 text-sm text-ink-muted">No estimates yet.</p>
                ) : (
                    <div className="mt-3 divide-y divide-ink-line border border-ink-line bg-white">
                        {history.map((e) => (
                            <div key={e.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                                <button onClick={() => openEstimate(e.id)} className="min-w-0 flex-1 text-left">
                                    <div className="truncate text-sm font-semibold text-navy hover:text-gold">
                                        {e.project_name || "Estimate"}
                                    </div>
                                    <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                        {e.role} · {e.country} · {e.created_at ? new Date(e.created_at).toLocaleString() : ""}
                                        {e.result?.visible?.final_amount ? ` · ${e.result.visible.final_amount}` : ""}
                                    </div>
                                </button>
                                <span
                                    className={`border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${
                                        e.status === "failed"
                                            ? "border-destructive text-destructive"
                                            : e.status === "processing"
                                              ? "border-gold text-navy"
                                              : "border-success text-success"
                                    }`}
                                >
                                    {e.status || "complete"}
                                </span>
                                <button
                                    onClick={() => deleteEstimate(e.id)}
                                    className="text-ink-muted hover:text-destructive"
                                    aria-label="Delete estimate"
                                >
                                    <Trash2 size={14} />
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
