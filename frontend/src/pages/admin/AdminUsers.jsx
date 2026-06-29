import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Activity, CheckCircle2, KeyRound, Layers, Plus, ShieldOff, UserPlus, Users } from "lucide-react";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from "@/components/ui/dialog";
import { AdminLayout, Kpi } from "./AdminLayout";
import { useAuth } from "@/context/AuthContext";

export function AdminUsers() {
    const nav = useNavigate();
    const { startImpersonation } = useAuth();
    const [users, setUsers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [analytics, setAnalytics] = useState(null);
    const [search, setSearch] = useState("");
    const [createOpen, setCreateOpen] = useState(false);
    const [resetTarget, setResetTarget] = useState(null);
    const [submitting, setSubmitting] = useState(false);

    const load = async () => {
        try {
            const [u, a] = await Promise.all([
                api.get("/api/admin/users"),
                api.get("/api/admin/analytics"),
            ]);
            setUsers(u.data.items);
            setAnalytics(a.data);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setLoading(false);
    };

    useEffect(() => {
        load();
    }, []);

    const updateUser = async (uid, patch) => {
        try {
            await api.put(`/api/admin/users/${uid}`, patch);
            toast.success("User updated.");
            load();
        } catch (e) {
            toast.error(errMessage(e));
        }
    };

    const toggleActive = (uid, active) =>
        updateUser(uid, { is_active: active });

    const filtered = users.filter((u) => {
        if (!search) return true;
        const s = search.toLowerCase();
        return (
            (u.email || "").toLowerCase().includes(s) ||
            `${u.first_name} ${u.last_name}`.toLowerCase().includes(s) ||
            (u.role || "").toLowerCase().includes(s)
        );
    });

    return (
        <AdminLayout title="Users">
            <div className="grid gap-6 md:grid-cols-4">
                <Kpi label="TOTAL USERS" value={analytics?.total_users || 0} icon={Users} />
                <Kpi label="ACTIVE USERS" value={analytics?.active_users || 0} icon={CheckCircle2} />
                <Kpi label="PROJECTS" value={analytics?.total_projects || 0} icon={Layers} />
                <Kpi label="ANALYSES" value={analytics?.total_analyses || 0} icon={Activity} />
            </div>

            <div className="mt-8 card-steel">
                <div className="flex items-center justify-between gap-4 border-b border-ink-line px-6 py-4">
                    <div className="font-heading text-lg font-bold uppercase tracking-wide text-navy">
                        All users
                    </div>
                    <div className="flex items-center gap-3">
                        <Input
                            data-testid="admin-user-search"
                            placeholder="SEARCH BY EMAIL, NAME, ROLE…"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            className="h-9 w-72 rounded-none border-ink-line font-mono text-[11px] uppercase tracking-wider focus-visible:border-gold focus-visible:ring-0"
                        />
                        <button
                            onClick={() => setCreateOpen(true)}
                            data-testid="admin-create-user-btn"
                            className="btn-gold"
                        >
                            <UserPlus size={14} />
                            Create user
                        </button>
                    </div>
                </div>
                {loading ? (
                    <Skeleton className="m-6 h-64 rounded-none" />
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-left">
                            <thead className="bg-background">
                                <tr className="border-b border-ink-line">
                                    {["Name", "Email", "Role", "Active", "Verified", "Used", "Created", "Actions"].map((h) => (
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
                                {filtered.map((u) => (
                                    <tr
                                        key={u.id}
                                        data-testid={`admin-user-${u.id}`}
                                        className="border-b border-ink-line last:border-b-0"
                                    >
                                        <td className="px-4 py-3 font-heading text-sm font-semibold uppercase tracking-wide text-navy">
                                            {u.first_name} {u.last_name}
                                        </td>
                                        <td className="px-4 py-3 font-mono text-xs text-ink-muted">
                                            {u.email}
                                        </td>
                                        <td className="px-4 py-3">
                                            <Select
                                                value={u.role}
                                                onValueChange={(v) => updateUser(u.id, { role: v })}
                                                disabled={u.role === "super_admin"}
                                            >
                                                <SelectTrigger
                                                    data-testid={`role-select-${u.id}`}
                                                    className="h-9 w-36 rounded-none border-ink-line font-mono text-[11px] uppercase"
                                                >
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent className="rounded-none">
                                                    {["detailer", "fabricator", "super_admin"].map(
                                                        (r) => (
                                                            <SelectItem
                                                                key={r}
                                                                value={r}
                                                                disabled={r === "super_admin"}
                                                                className="rounded-none font-mono text-[11px] uppercase"
                                                            >
                                                                {r.replace("_", " ")}
                                                            </SelectItem>
                                                        ),
                                                    )}
                                                </SelectContent>
                                            </Select>
                                        </td>
                                        <td className="px-4 py-3">
                                            <button
                                                onClick={() => toggleActive(u.id, !u.is_active)}
                                                data-testid={`toggle-active-${u.id}`}
                                                disabled={u.role === "super_admin"}
                                                className={`inline-flex items-center gap-1 border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${
                                                    u.is_active
                                                        ? "border-success text-success"
                                                        : "border-destructive text-destructive"
                                                } ${u.role === "super_admin" ? "opacity-50" : ""}`}
                                            >
                                                {u.is_active ? "Active" : "Disabled"}
                                            </button>
                                        </td>
                                        <td className="px-4 py-3 font-mono text-[10px] uppercase tracking-wider">
                                            {u.is_verified ? (
                                                <span className="text-success">✓ Verified</span>
                                            ) : (
                                                <span className="text-warning">Pending</span>
                                            )}
                                        </td>
                                        <td className="px-4 py-3 font-mono text-[10px] text-navy">
                                            {u.analyses_this_month || 0}
                                        </td>
                                        <td className="px-4 py-3 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                            {new Date(u.created_at).toLocaleDateString()}
                                        </td>
                                        <td className="px-4 py-3">
                                            <div className="flex items-center gap-2">
                                                <button
                                                    onClick={() => nav(`/admin/permissions/${u.id}`)}
                                                    data-testid={`edit-perms-${u.id}`}
                                                    disabled={u.role === "super_admin"}
                                                    className="inline-flex items-center gap-1 border border-navy bg-white px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-navy hover:bg-navy hover:text-gold disabled:opacity-40"
                                                >
                                                    <Plus size={10} />
                                                    Permissions
                                                </button>
                                                <button
                                                    onClick={() => setResetTarget(u)}
                                                    data-testid={`reset-pw-${u.id}`}
                                                    className="inline-flex items-center gap-1 border border-ink-line bg-white px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-ink-muted hover:border-warning hover:text-warning"
                                                >
                                                    <KeyRound size={10} />
                                                    Reset
                                                </button>
                                                {u.role !== "super_admin" && (
                                                    <button
                                                        onClick={async () => {
                                                            try {
                                                                await startImpersonation(u.id);
                                                                toast.success(`Now viewing as ${u.email}`);
                                                                nav("/dashboard");
                                                            } catch (e) {
                                                                toast.error(errMessage(e));
                                                            }
                                                        }}
                                                        data-testid={`impersonate-${u.id}`}
                                                        className="inline-flex items-center gap-1 border border-ink-line bg-white px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-ink-muted hover:border-destructive hover:text-destructive"
                                                    >
                                                        <ShieldOff size={10} />
                                                        View as
                                                    </button>
                                                )}
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            <CreateUserDialog
                open={createOpen}
                onOpenChange={setCreateOpen}
                submitting={submitting}
                onSubmit={async (payload) => {
                    setSubmitting(true);
                    try {
                        await api.post("/api/admin/users", payload);
                        toast.success("User created.");
                        setCreateOpen(false);
                        load();
                    } catch (e) {
                        toast.error(errMessage(e));
                    }
                    setSubmitting(false);
                }}
            />

            <ResetPasswordDialog
                target={resetTarget}
                onClose={() => setResetTarget(null)}
                onSubmit={async (newPw) => {
                    try {
                        await api.post(`/api/admin/users/${resetTarget.id}/password-reset`, {
                            new_password: newPw,
                        });
                        toast.success(`Password reset for ${resetTarget.email}.`);
                        setResetTarget(null);
                    } catch (e) {
                        toast.error(errMessage(e));
                    }
                }}
            />
        </AdminLayout>
    );
}

function CreateUserDialog({ open, onOpenChange, onSubmit, submitting }) {
    const [form, setForm] = useState({
        first_name: "",
        last_name: "",
        email: "",
        company: "",
        country: "",
        password: "",
        role: "detailer",
    });
    useEffect(() => {
        if (open)
            setForm({
                first_name: "",
                last_name: "",
                email: "",
                company: "",
                country: "",
                password: "",
                role: "detailer",
            });
    }, [open]);

    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent
                data-testid="create-user-dialog"
                className="max-w-lg rounded-none border-ink-line"
            >
                <DialogHeader>
                    <DialogTitle className="font-heading text-xl uppercase tracking-tight text-navy">
                        Create user
                    </DialogTitle>
                </DialogHeader>
                <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                        <Field label="First name">
                            <Input
                                data-testid="new-user-first"
                                value={form.first_name}
                                onChange={(e) => set("first_name", e.target.value)}
                            />
                        </Field>
                        <Field label="Last name">
                            <Input
                                data-testid="new-user-last"
                                value={form.last_name}
                                onChange={(e) => set("last_name", e.target.value)}
                            />
                        </Field>
                    </div>
                    <Field label="Email">
                        <Input
                            data-testid="new-user-email"
                            type="email"
                            value={form.email}
                            onChange={(e) => set("email", e.target.value)}
                        />
                    </Field>
                    <div className="grid grid-cols-2 gap-3">
                        <Field label="Company">
                            <Input
                                data-testid="new-user-company"
                                value={form.company}
                                onChange={(e) => set("company", e.target.value)}
                            />
                        </Field>
                        <Field label="Country">
                            <Input
                                data-testid="new-user-country"
                                value={form.country}
                                onChange={(e) => set("country", e.target.value)}
                            />
                        </Field>
                    </div>
                    <Field label="Role">
                        <Select value={form.role} onValueChange={(v) => set("role", v)}>
                            <SelectTrigger
                                data-testid="new-user-role"
                                className="h-10 rounded-none border-ink-line font-mono text-[11px] uppercase"
                            >
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent className="rounded-none">
                                <SelectItem className="rounded-none font-mono text-[11px] uppercase" value="detailer">
                                    Detailer
                                </SelectItem>
                                <SelectItem className="rounded-none font-mono text-[11px] uppercase" value="fabricator">
                                    Fabricator
                                </SelectItem>
                            </SelectContent>
                        </Select>
                    </Field>
                    <Field label="Initial password (≥8 chars, 1 upper, 1 digit, 1 symbol)">
                        <Input
                            data-testid="new-user-password"
                            type="text"
                            value={form.password}
                            onChange={(e) => set("password", e.target.value)}
                        />
                    </Field>
                </div>
                <DialogFooter>
                    <button onClick={() => onOpenChange(false)} className="btn-ghost-navy">
                        Cancel
                    </button>
                    <button
                        onClick={() => onSubmit(form)}
                        disabled={submitting}
                        data-testid="new-user-submit"
                        className="btn-gold"
                    >
                        {submitting ? "Creating…" : "Create user"}
                    </button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

function ResetPasswordDialog({ target, onClose, onSubmit }) {
    const [pw, setPw] = useState("");
    useEffect(() => {
        if (target) setPw("");
    }, [target]);
    if (!target) return null;
    return (
        <Dialog open={!!target} onOpenChange={(o) => !o && onClose()}>
            <DialogContent
                data-testid="reset-password-dialog"
                className="max-w-md rounded-none border-ink-line"
            >
                <DialogHeader>
                    <DialogTitle className="font-heading text-xl uppercase tracking-tight text-navy">
                        Reset password
                    </DialogTitle>
                </DialogHeader>
                <p className="text-sm text-ink-muted">
                    Set a new password for{" "}
                    <span className="font-mono text-navy">{target.email}</span>. Communicate it
                    out-of-band — they should change it on next login.
                </p>
                <Input
                    data-testid="reset-pw-input"
                    placeholder="New password (≥8 chars, 1 upper, 1 digit, 1 symbol)"
                    value={pw}
                    onChange={(e) => setPw(e.target.value)}
                />
                <DialogFooter>
                    <button onClick={onClose} className="btn-ghost-navy">
                        Cancel
                    </button>
                    <button
                        onClick={() => onSubmit(pw)}
                        data-testid="reset-pw-submit"
                        className="btn-gold"
                    >
                        Reset
                    </button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

function Field({ label, children }) {
    return (
        <div>
            <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                {label}
            </label>
            {children}
        </div>
    );
}
