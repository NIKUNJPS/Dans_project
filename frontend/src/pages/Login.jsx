import { Link, useLocation, useNavigate } from "react-router-dom";
import { useState } from "react";
import { AlertCircle, ArrowRight, Eye, EyeOff, Lock, Mail, ShieldCheck } from "lucide-react";
import { Logo } from "@/components/brand/Logo";
import { RolePillSelector } from "@/components/brand/RolePillSelector";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";

export default function Login() {
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [role, setRole] = useState("detailer");
    const [showPw, setShowPw] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const { login } = useAuth();
    const nav = useNavigate();
    const loc = useLocation();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError("");
        setLoading(true);
        try {
            const { data } = await api.post("/api/auth/login", { email, password });
            if (data?.mfa_required) {
                toast.info("Please verify your email — we sent a fresh code.");
                nav(`/verify-otp?user_id=${data.user_id}&email=${encodeURIComponent(data.email)}`);
                return;
            }
            login(
                { access_token: data.access_token, refresh_token: data.refresh_token },
                data.user,
            );
            toast.success(`Welcome back, ${data.user.first_name}.`);
            const target = loc.state?.from || "/dashboard";
            nav(target);
        } catch (err) {
            setError(errMessage(err));
        } finally {
            setLoading(false);
        }
    };

    return (
        <div data-testid="login-page" className="grid min-h-screen grid-cols-1 md:grid-cols-12">
            {/* LEFT — dark brand panel */}
            <div className="relative hidden min-h-screen overflow-hidden bg-navy md:col-span-7 md:block">
                <div
                    className="absolute inset-0"
                    style={{
                        backgroundImage:
                            "url('https://images.pexels.com/photos/4458205/pexels-photo-4458205.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940')",
                        backgroundSize: "cover",
                        backgroundPosition: "center",
                        opacity: 0.25,
                    }}
                />
                <div className="absolute inset-0 bg-gradient-to-br from-navy via-navy/85 to-navy-mid/60" />
                <div className="absolute inset-0 tech-grid opacity-40" />
                <div className="relative z-10 flex h-full flex-col justify-between p-14 text-white">
                    <Logo variant="light" size="md" />
                    <div>
                        <div className="text-overline mb-6">Structural Intelligence</div>
                        <div className="font-heading text-5xl font-black uppercase leading-[0.95] tracking-tight md:text-6xl">
                            Drawings in.<br />
                            <span className="text-gold">Answers out.</span>
                        </div>
                        <p className="mt-6 max-w-md text-lg text-white/70">
                            Trusted by detailers across US · Canada · Australia · India · APAC.
                        </p>
                        <div className="mt-10 grid max-w-lg grid-cols-3 gap-0 border border-white/10">
                            <div className="border-r border-white/10 px-4 py-5">
                                <div className="font-heading text-3xl font-black text-gold">
                                    96.4%
                                </div>
                                <div className="font-mono text-[10px] uppercase tracking-wider text-white/60">
                                    Auto QC
                                </div>
                            </div>
                            <div className="border-r border-white/10 px-4 py-5">
                                <div className="font-heading text-3xl font-black text-gold">
                                    25
                                </div>
                                <div className="font-mono text-[10px] uppercase tracking-wider text-white/60">
                                    Modes
                                </div>
                            </div>
                            <div className="px-4 py-5">
                                <div className="font-heading text-3xl font-black text-gold">
                                    500 MB
                                </div>
                                <div className="font-mono text-[10px] uppercase tracking-wider text-white/60">
                                    Packages
                                </div>
                            </div>
                        </div>
                    </div>
                    <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/50">
                        AISC 360-22 · AWS D1.1 · ASTM · RCSC · SSPC · OSHA
                    </div>
                </div>
            </div>

            {/* RIGHT — form */}
            <div className="flex min-h-screen flex-col justify-center bg-white px-6 py-10 md:col-span-5 md:px-16">
                <div className="mx-auto w-full max-w-md">
                    <div className="mb-8 md:hidden">
                        <Logo variant="dark" size="md" />
                    </div>
                    <h1 className="font-heading text-4xl font-black leading-tight tracking-tight text-navy">
                        Welcome back
                    </h1>
                    <p className="mt-2 text-sm text-ink-muted">
                        Sign in to the most advanced structural steel intelligence platform.
                    </p>

                    <div className="mt-6">
                        <div className="mb-3 text-overline">Role (UI personalisation)</div>
                        <RolePillSelector value={role} onChange={setRole} />
                    </div>

                    <form onSubmit={handleSubmit} className="mt-8 space-y-5" data-testid="login-form">
                        <div>
                            <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                Email
                            </label>
                            <div className="relative">
                                <Mail size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" />
                                <Input
                                    data-testid="login-email"
                                    type="email"
                                    autoComplete="email"
                                    required
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    className="h-12 rounded-none border-ink-line pl-9 font-body focus-visible:border-gold focus-visible:ring-0"
                                />
                            </div>
                        </div>
                        <div>
                            <div className="mb-1 flex items-center justify-between">
                                <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                    Password
                                </label>
                                <Link
                                    to="/forgot-password"
                                    data-testid="forgot-password-link"
                                    className="font-mono text-[10px] uppercase tracking-[0.2em] text-gold hover:text-navy"
                                >
                                    Forgot?
                                </Link>
                            </div>
                            <div className="relative">
                                <Lock size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" />
                                <Input
                                    data-testid="login-password"
                                    type={showPw ? "text" : "password"}
                                    autoComplete="current-password"
                                    required
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    className="h-12 rounded-none border-ink-line pl-9 font-body focus-visible:border-gold focus-visible:ring-0"
                                />
                                <button
                                    type="button"
                                    onClick={() => setShowPw((v) => !v)}
                                    data-testid="toggle-password"
                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-muted hover:text-navy"
                                    aria-label="Toggle password"
                                >
                                    {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                                </button>
                            </div>
                        </div>

                        {error && (
                            <div
                                data-testid="login-error"
                                className="flex items-center gap-2 border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
                            >
                                <AlertCircle size={14} />
                                {error}
                            </div>
                        )}

                        <button
                            type="submit"
                            disabled={loading}
                            data-testid="login-submit-btn"
                            className="btn-navy w-full"
                        >
                            {loading ? "Signing in…" : "Enter Portal"}
                            <ArrowRight size={16} />
                        </button>
                    </form>

                    <div className="mt-6 flex items-center gap-2 border border-gold/30 bg-gold-pale px-3 py-2 font-mono text-[11px] uppercase tracking-wider text-navy">
                        <ShieldCheck size={14} className="text-gold" />
                        Login secured with SHA-256 audit trail
                    </div>

                    <p className="mt-8 text-center text-sm text-ink-muted">
                        New to 4XStruct?{" "}
                        <Link
                            to="/signup"
                            data-testid="goto-signup-link"
                            className="font-semibold text-navy hover:text-gold"
                        >
                            Request access →
                        </Link>
                    </p>
                </div>
            </div>
        </div>
    );
}
