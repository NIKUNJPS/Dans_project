import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { OtpInput } from "@/components/brand/OtpInput";
import { ShieldCheck, User2 } from "lucide-react";

const TABS = [
    { to: "/settings/profile", label: "Profile" },
    { to: "/settings/security", label: "Security" },
];

function SettingsLayout({ children }) {
    const loc = useLocation();
    return (
        <div className="min-h-full bg-background">
            <div className="border-b border-ink-line bg-white px-10 py-10">
                <div className="text-overline">Settings</div>
                <h1 className="mt-2 font-heading text-5xl font-black leading-none tracking-tight text-navy">
                    Your account
                </h1>
                <div className="mt-6 flex gap-6 border-b border-ink-line">
                    {TABS.map((t) => {
                        const active = loc.pathname === t.to;
                        return (
                            <Link
                                key={t.to}
                                to={t.to}
                                className={`border-b-2 pb-3 font-heading text-sm uppercase tracking-wider transition-colors ${
                                    active
                                        ? "border-gold text-navy"
                                        : "border-transparent text-ink-muted hover:text-navy"
                                }`}
                            >
                                {t.label}
                            </Link>
                        );
                    })}
                </div>
            </div>
            <div className="container-steel py-10">{children}</div>
        </div>
    );
}

export function SettingsProfile() {
    const { user, refreshUser } = useAuth();
    const [form, setForm] = useState({
        first_name: user?.first_name || "",
        last_name: user?.last_name || "",
        company: user?.company || "",
        country: user?.country || "",
        phone: user?.phone || "",
    });
    const [saving, setSaving] = useState(false);

    const save = async (e) => {
        e.preventDefault();
        setSaving(true);
        try {
            await api.put("/api/auth/me", form);
            await refreshUser();
            toast.success("Profile updated.");
        } catch (err) {
            toast.error(errMessage(err));
        }
        setSaving(false);
    };

    return (
        <SettingsLayout>
            <form
                onSubmit={save}
                className="card-steel max-w-2xl p-8"
                data-testid="profile-form"
            >
                <div className="mb-6 flex items-center gap-3 border-b border-ink-line pb-4">
                    <User2 size={18} className="text-gold" />
                    <div className="font-heading text-xl font-bold uppercase tracking-tight text-navy">
                        Profile
                    </div>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                    <Field
                        label="First name"
                        value={form.first_name}
                        onChange={(v) => setForm((f) => ({ ...f, first_name: v }))}
                        testId="profile-first"
                    />
                    <Field
                        label="Last name"
                        value={form.last_name}
                        onChange={(v) => setForm((f) => ({ ...f, last_name: v }))}
                        testId="profile-last"
                    />
                    <Field
                        label="Company"
                        value={form.company}
                        onChange={(v) => setForm((f) => ({ ...f, company: v }))}
                        testId="profile-company"
                    />
                    <Field
                        label="Country"
                        value={form.country}
                        onChange={(v) => setForm((f) => ({ ...f, country: v }))}
                        testId="profile-country"
                    />
                    <Field
                        label="Phone"
                        value={form.phone}
                        onChange={(v) => setForm((f) => ({ ...f, phone: v }))}
                        testId="profile-phone"
                    />
                    <Field
                        label="Email (read-only)"
                        value={user?.email}
                        onChange={() => {}}
                        disabled
                    />
                </div>
                <div className="mt-6 flex items-center gap-3">
                    <button
                        type="submit"
                        disabled={saving}
                        data-testid="save-profile-btn"
                        className="btn-navy"
                    >
                        {saving ? "Saving…" : "Save changes"}
                    </button>
                    <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                        Role: {user?.role} · Tier: {user?.subscription_tier}
                    </div>
                </div>
            </form>
        </SettingsLayout>
    );
}

export function SettingsSecurity() {
    const [currentPw, setCurrentPw] = useState("");
    const [newPw, setNewPw] = useState("");
    const [confirmPw, setConfirmPw] = useState("");
    const [otpSent, setOtpSent] = useState(false);
    const [otp, setOtp] = useState("");
    const [loading, setLoading] = useState(false);

    const requestChange = async () => {
        if (newPw !== confirmPw) {
            toast.error("Passwords do not match");
            return;
        }
        setLoading(true);
        try {
            const { data } = await api.post("/api/auth/change-password", {
                current_password: currentPw,
                new_password: newPw,
            });
            if (data.otp_required) {
                setOtpSent(true);
                toast.success("Check your email for the confirmation code.");
            } else {
                toast.success("Password updated.");
            }
        } catch (e) {
            toast.error(errMessage(e));
        }
        setLoading(false);
    };

    const confirm = async () => {
        setLoading(true);
        try {
            await api.post("/api/auth/change-password", {
                current_password: currentPw,
                new_password: newPw,
                otp,
            });
            toast.success("Password changed successfully.");
            setCurrentPw("");
            setNewPw("");
            setConfirmPw("");
            setOtp("");
            setOtpSent(false);
        } catch (e) {
            toast.error(errMessage(e));
        }
        setLoading(false);
    };

    return (
        <SettingsLayout>
            <div className="card-steel max-w-2xl p-8" data-testid="security-panel">
                <div className="mb-6 flex items-center gap-3 border-b border-ink-line pb-4">
                    <ShieldCheck size={18} className="text-gold" />
                    <div className="font-heading text-xl font-bold uppercase tracking-tight text-navy">
                        Change password
                    </div>
                </div>
                {!otpSent ? (
                    <div className="space-y-4">
                        <Field
                            label="Current password"
                            value={currentPw}
                            onChange={setCurrentPw}
                            type="password"
                            testId="sec-current-pw"
                        />
                        <Field
                            label="New password"
                            value={newPw}
                            onChange={setNewPw}
                            type="password"
                            testId="sec-new-pw"
                        />
                        <Field
                            label="Confirm new password"
                            value={confirmPw}
                            onChange={setConfirmPw}
                            type="password"
                            testId="sec-confirm-pw"
                        />
                        <button
                            type="button"
                            onClick={requestChange}
                            disabled={loading || !currentPw || !newPw}
                            data-testid="request-change-pw-btn"
                            className="btn-navy"
                        >
                            {loading ? "Sending OTP…" : "Continue"}
                        </button>
                    </div>
                ) : (
                    <div className="space-y-4">
                        <p className="text-sm text-ink-muted">
                            Enter the 6-digit code we emailed you to confirm the change.
                        </p>
                        <OtpInput value={otp} onChange={setOtp} />
                        <button
                            type="button"
                            onClick={confirm}
                            disabled={loading || otp.length !== 6}
                            data-testid="confirm-change-pw-btn"
                            className="btn-gold"
                        >
                            {loading ? "Confirming…" : "Confirm & update"}
                        </button>
                    </div>
                )}
            </div>
        </SettingsLayout>
    );
}

function Field({ label, value, onChange, disabled = false, type = "text", testId }) {
    return (
        <div>
            <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                {label}
            </label>
            <Input
                data-testid={testId}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                disabled={disabled}
                type={type}
                className="h-11 rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0 disabled:bg-background"
            />
        </div>
    );
}
