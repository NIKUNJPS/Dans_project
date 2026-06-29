import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Save, ShieldCheck } from "lucide-react";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { AdminLayout } from "./AdminLayout";

const EXPORTS = ["pdf", "word", "excel", "csv", "markdown"];
const COUNTRIES = [
    "USA", "Canada", "UK", "UAE", "Australia",
    "India", "Europe", "Saudi Arabia", "Singapore",
];

const BOOLEAN_FLAGS = [
    ["canUploadFiles",     "Can upload files"],
    ["canCreateProjects",  "Can create projects"],
    ["canViewHistory",     "Can view analysis history"],
    ["canDownloadReports", "Can download reports"],
    ["canSendRfis",        "Can send RFIs"],
    ["canViewAuditLog",    "Can view audit log"],
    ["blockchainAnchoring","Blockchain anchoring"],
    ["canRunEstimation",   "Can run estimation"],
    ["showDashboardStats", "Show dashboard stats"],
    ["showActivityChart",  "Show activity chart"],
];


export function AdminPermissionsList() {
    const nav = useNavigate();
    const [users, setUsers] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/api/admin/users");
                setUsers(data.items.filter((u) => u.role !== "super_admin"));
            } catch (e) {
                toast.error(errMessage(e));
            }
            setLoading(false);
        })();
    }, []);

    return (
        <AdminLayout title="Permissions">
            <div className="card-steel">
                <div className="flex items-center justify-between border-b border-ink-line px-6 py-4">
                    <div>
                        <div className="font-heading text-lg font-bold uppercase tracking-wide text-navy">
                            Per-user permission matrix
                        </div>
                        <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                            Select a user to edit their granular feature permissions.
                        </div>
                    </div>
                </div>
                {loading ? (
                    <Skeleton className="m-6 h-64 rounded-none" />
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-left">
                            <thead className="bg-background">
                                <tr className="border-b border-ink-line">
                                    {["Name", "Email", "Role", "Status", ""].map((h) => (
                                        <th
                                            key={h}
                                            className="px-4 py-3 font-mono text-[10px] uppercase tracking-wider text-ink-muted"
                                        >
                                            {h}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {users.map((u) => (
                                    <tr
                                        key={u.id}
                                        data-testid={`perm-row-${u.id}`}
                                        className="border-b border-ink-line last:border-b-0 hover:bg-background"
                                    >
                                        <td className="px-4 py-3 font-heading text-sm font-semibold uppercase tracking-wide text-navy">
                                            {u.first_name} {u.last_name}
                                        </td>
                                        <td className="px-4 py-3 font-mono text-xs text-ink-muted">{u.email}</td>
                                        <td className="px-4 py-3 font-mono text-[11px] uppercase tracking-wider text-navy">{u.role}</td>
                                        <td className="px-4 py-3 font-mono text-[10px] uppercase tracking-wider">
                                            {u.is_active ? <span className="text-success">Active</span> : <span className="text-destructive">Disabled</span>}
                                        </td>
                                        <td className="px-4 py-3">
                                            <button
                                                onClick={() => nav(`/admin/permissions/${u.id}`)}
                                                data-testid={`edit-perms-row-${u.id}`}
                                                className="inline-flex items-center gap-1 border border-navy bg-white px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-navy hover:bg-navy hover:text-gold"
                                            >
                                                Edit permissions
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                                {!users.length && (
                                    <tr>
                                        <td colSpan={5} className="px-6 py-12 text-center text-sm text-ink-muted">
                                            No detailer or fabricator users yet. Create one from the Users tab.
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </AdminLayout>
    );
}


export function AdminPermissionEditor() {
    const { uid } = useParams();
    const nav = useNavigate();
    const [user, setUser] = useState(null);
    const [perms, setPerms] = useState(null);
    const [allModes, setAllModes] = useState([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        (async () => {
            try {
                const [u, m] = await Promise.all([
                    api.get(`/api/admin/users/${uid}`),
                    api.get("/api/admin/modes/all"),
                ]);
                setUser(u.data.user);
                setPerms(u.data.permissions);
                setAllModes(m.data.modes || []);
            } catch (e) {
                toast.error(errMessage(e));
            }
            setLoading(false);
        })();
    }, [uid]);

    const modesForRole = useMemo(
        () => allModes.filter((m) => !user || m.role === user.role),
        [allModes, user],
    );

    const setField = (k, v) => setPerms((p) => ({ ...p, [k]: v }));

    const toggleListItem = (key, value) => {
        setPerms((p) => {
            const arr = new Set(p?.[key] || []);
            if (arr.has(value)) arr.delete(value);
            else arr.add(value);
            return { ...p, [key]: [...arr] };
        });
    };

    const save = async () => {
        setSaving(true);
        try {
            const payload = {
                analysesPerMonth:     perms.analysesPerMonth,
                maxFileSizeMb:        perms.maxFileSizeMb,
                maxFilesPerAnalysis:  perms.maxFilesPerAnalysis,
                allowedModes:         perms.allowedModes || [],
                allowedExports:       perms.allowedExports || [],
                estimationCountries:  perms.estimationCountries || [],
                ...Object.fromEntries(BOOLEAN_FLAGS.map(([k]) => [k, !!perms[k]])),
            };
            await api.put(`/api/admin/users/${uid}/permissions`, payload);
            toast.success("Permissions saved.");
        } catch (e) {
            toast.error(errMessage(e));
        }
        setSaving(false);
    };

    if (loading || !user || !perms) {
        return (
            <AdminLayout title="Permissions">
                <Skeleton className="h-64 rounded-none" />
            </AdminLayout>
        );
    }

    return (
        <AdminLayout title={`Permissions · ${user.first_name} ${user.last_name}`}>
            <div className="flex items-center justify-between">
                <button
                    onClick={() => nav("/admin/permissions")}
                    data-testid="back-perms-list"
                    className="inline-flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-navy hover:text-gold"
                >
                    <ArrowLeft size={14} /> Back to permissions
                </button>
                <button
                    onClick={save}
                    disabled={saving}
                    data-testid="save-perms-btn"
                    className="btn-gold"
                >
                    <Save size={14} />
                    {saving ? "Saving…" : "Save permissions"}
                </button>
            </div>

            <div className="mt-6 grid gap-6 lg:grid-cols-3">
                {/* CAPS */}
                <Section title="Usage caps">
                    <NumberField
                        label="Analyses per month (−1 = unlimited)"
                        testId="perm-monthly-cap"
                        value={perms.analysesPerMonth}
                        onChange={(v) => setField("analysesPerMonth", v)}
                    />
                    <NumberField
                        label="Max file size (MB)"
                        testId="perm-file-cap"
                        value={perms.maxFileSizeMb}
                        onChange={(v) => setField("maxFileSizeMb", v)}
                    />
                    <NumberField
                        label="Max files per analysis"
                        testId="perm-files-per-analysis"
                        value={perms.maxFilesPerAnalysis}
                        onChange={(v) => setField("maxFilesPerAnalysis", v)}
                    />
                </Section>

                {/* FEATURE FLAGS */}
                <Section title="Feature flags">
                    {BOOLEAN_FLAGS.map(([k, label]) => (
                        <ToggleRow
                            key={k}
                            label={label}
                            testId={`perm-${k}`}
                            checked={!!perms[k]}
                            onChange={(v) => setField(k, v)}
                        />
                    ))}
                </Section>

                {/* EXPORTS */}
                <Section title="Allowed export formats">
                    {EXPORTS.map((fmt) => (
                        <ToggleRow
                            key={fmt}
                            label={fmt.toUpperCase()}
                            testId={`perm-export-${fmt}`}
                            checked={(perms.allowedExports || []).includes(fmt)}
                            onChange={() => toggleListItem("allowedExports", fmt)}
                        />
                    ))}
                </Section>
            </div>

            {/* COUNTRIES */}
            <Section title="Estimation countries" wide className="mt-6">
                <div className="grid grid-cols-2 gap-x-6 gap-y-2 md:grid-cols-3">
                    {COUNTRIES.map((c) => (
                        <ToggleRow
                            key={c}
                            label={c}
                            testId={`perm-country-${c.toLowerCase().replace(/\s/g, "-")}`}
                            checked={(perms.estimationCountries || []).includes(c)}
                            onChange={() => toggleListItem("estimationCountries", c)}
                        />
                    ))}
                </div>
            </Section>

            {/* MODES */}
            <Section title={`Analysis modes (${user.role})`} wide className="mt-6">
                {modesForRole.length === 0 ? (
                    <div className="text-sm text-ink-muted">
                        No modes are defined yet for the {user.role} role. Add modes in
                        <code className="mx-1 bg-background px-1">prompts/{user.role}_prompts.py</code>
                        and they'll appear here.
                    </div>
                ) : (
                    <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                        {modesForRole.map((m) => {
                            const enabled = (perms.allowedModes || []).includes(m.id) ||
                                (perms.allowedModes || []).includes("*");
                            return (
                                <div
                                    key={m.id}
                                    data-testid={`mode-toggle-${m.id}`}
                                    className={`flex items-start justify-between gap-3 border p-3 ${
                                        enabled ? "border-gold bg-gold-pale" : "border-ink-line bg-white"
                                    }`}
                                >
                                    <div className="min-w-0 flex-1">
                                        <div className="font-mono text-[9px] uppercase tracking-wider text-gold">
                                            {m.group}
                                        </div>
                                        <div className="font-heading text-sm font-bold uppercase tracking-tight text-navy">
                                            {m.label}
                                        </div>
                                        <div className="mt-1 line-clamp-2 text-xs text-ink-muted">
                                            {m.description}
                                        </div>
                                    </div>
                                    <Switch
                                        data-testid={`mode-switch-${m.id}`}
                                        checked={enabled}
                                        onCheckedChange={() => toggleListItem("allowedModes", m.id)}
                                    />
                                </div>
                            );
                        })}
                    </div>
                )}
            </Section>

            <div className="mt-6 flex items-center gap-2 border border-ink-line bg-white p-3 font-mono text-[11px] uppercase tracking-wider text-ink-muted">
                <ShieldCheck size={14} className="text-gold" />
                Last updated:{" "}
                <span className="text-navy">
                    {perms.updatedAt ? new Date(perms.updatedAt).toLocaleString() : "—"}
                </span>
            </div>
        </AdminLayout>
    );
}

function Section({ title, children, wide = false, className = "" }) {
    return (
        <div className={`card-steel p-5 ${wide ? "col-span-full" : ""} ${className}`}>
            <div className="mb-4 font-heading text-sm font-bold uppercase tracking-wide text-navy">
                {title}
            </div>
            <div className="space-y-2">{children}</div>
        </div>
    );
}

function NumberField({ label, value, onChange, testId }) {
    return (
        <div>
            <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                {label}
            </label>
            <Input
                data-testid={testId}
                type="number"
                value={value ?? 0}
                onChange={(e) => onChange(parseInt(e.target.value || "0", 10))}
                className="h-10 rounded-none border-ink-line font-mono text-sm focus-visible:border-gold focus-visible:ring-0"
            />
        </div>
    );
}

function ToggleRow({ label, checked, onChange, testId }) {
    return (
        <div className="flex items-center justify-between border-b border-ink-line py-1.5 last:border-b-0">
            <div className="font-mono text-[11px] uppercase tracking-wider text-ink">{label}</div>
            <Switch data-testid={testId} checked={checked} onCheckedChange={onChange} />
        </div>
    );
}
