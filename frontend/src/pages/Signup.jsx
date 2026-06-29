import { Link, useNavigate } from "react-router-dom";
import { useMemo, useState } from "react";
import { AlertCircle, ArrowRight, Eye, EyeOff, Lock, Mail, User } from "lucide-react";
import { Logo } from "@/components/brand/Logo";
import { RolePillSelector } from "@/components/brand/RolePillSelector";
import { api, errMessage } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";

function passwordScore(pw) {
    let s = 0;
    if (pw.length >= 8) s++;
    if (/[A-Z]/.test(pw)) s++;
    if (/\d/.test(pw)) s++;
    if (/[^A-Za-z0-9]/.test(pw)) s++;
    if (pw.length >= 12) s++;
    return s;
}

export default function Signup() {
    const [first, setFirst] = useState("");
    const [last, setLast] = useState("");
    const [email, setEmail] = useState("");
    const [company, setCompany] = useState("");
    const [country, setCountry] = useState("");
    const [password, setPassword] = useState("");
    const [confirm, setConfirm] = useState("");
    const [role, setRole] = useState("detailer");
    const [agree, setAgree] = useState(false);
    const [showPw, setShowPw] = useState(false);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const nav = useNavigate();

    const score = useMemo(() => passwordScore(password), [password]);
    const strengthLabels = ["Too short", "Weak", "Fair", "Strong", "Very strong", "Rock solid"];
    const strengthColors = [
        "bg-destructive",
        "bg-destructive",
        "bg-warning",
        "bg-success",
        "bg-gold",
        "bg-gold",
    ];

    const submit = async (e) => {
        e.preventDefault();
        setError("");
        if (password !== confirm) {
            setError("Passwords do not match");
            return;
        }
        if (!agree) {
            setError("Please accept the Terms and Privacy Policy");
            return;
        }
        setLoading(true);
        try {
            const { data } = await api.post("/api/auth/signup", {
                email,
                password,
                first_name: first,
                last_name: last,
                company,
                country,
                role,
            });
            toast.success("Check your email for the 6-digit code.");
            nav(
                `/verify-otp?user_id=${data.user_id}&email=${encodeURIComponent(data.email)}`,
            );
        } catch (err) {
            setError(errMessage(err));
        } finally {
            setLoading(false);
        }
    };

    return (
        <div
            data-testid="signup-page"
            className="grid min-h-screen grid-cols-1 md:grid-cols-12"
        >
            {/* LEFT BRAND */}
            <div className="relative hidden min-h-screen overflow-hidden bg-navy md:col-span-6 md:block">
                <div
                    className="absolute inset-0"
                    style={{
                        backgroundImage:
                            "url('https://images.unsplash.com/photo-1762146828422-50a8bd416d3c?crop=entropy&cs=srgb&fm=jpg&q=85')",
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
                        <div className="text-overline mb-6">Create your account</div>
                        <div className="font-heading text-5xl font-black uppercase leading-[0.95] tracking-tight md:text-6xl">
                            Start free.<br />
                            <span className="text-gold">Bid smarter.</span>
                        </div>
                        <p className="mt-6 max-w-md text-white/70">
                            5 analyses / month on the free tier. No credit card. Cancel anytime.
                        </p>
                    </div>
                    <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/50">
                        SHA-256 audit trail · STRUCTMIND CORE · SOC-2 roadmap
                    </div>
                </div>
            </div>

            {/* RIGHT — form */}
            <div className="flex min-h-screen flex-col justify-center bg-white px-6 py-10 md:col-span-6 md:px-16">
                <div className="mx-auto w-full max-w-lg">
                    <div className="mb-6 md:hidden">
                        <Logo variant="dark" size="md" />
                    </div>
                    <h1 className="font-heading text-4xl font-black leading-tight tracking-tight text-navy">
                        Create your account
                    </h1>
                    <p className="mt-2 text-sm text-ink-muted">
                        Takes under 90 seconds. We'll verify with a 6-digit code.
                    </p>

                    <form
                        onSubmit={submit}
                        className="mt-6 space-y-4"
                        data-testid="signup-form"
                    >
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                    First name
                                </label>
                                <Input
                                    data-testid="signup-first"
                                    required
                                    value={first}
                                    onChange={(e) => setFirst(e.target.value)}
                                    className="h-11 rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0"
                                />
                            </div>
                            <div>
                                <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                    Last name
                                </label>
                                <Input
                                    data-testid="signup-last"
                                    required
                                    value={last}
                                    onChange={(e) => setLast(e.target.value)}
                                    className="h-11 rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0"
                                />
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                    Company
                                </label>
                                <Input
                                    data-testid="signup-company"
                                    value={company}
                                    onChange={(e) => setCompany(e.target.value)}
                                    className="h-11 rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0"
                                />
                            </div>
                            <div>
                                <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                    Country
                                </label>
                                <Input
                                    data-testid="signup-country"
                                    value={country}
                                    onChange={(e) => setCountry(e.target.value)}
                                    className="h-11 rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0"
                                />
                            </div>
                        </div>
                        <div>
                            <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                Work email
                            </label>
                            <Input
                                data-testid="signup-email"
                                type="email"
                                required
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                className="h-11 rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0"
                            />
                        </div>
                        <div>
                            <label className="mb-2 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                Your role
                            </label>
                            <RolePillSelector
                                value={role}
                                onChange={setRole}
                                testIdPrefix="signup-role"
                            />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                    Password
                                </label>
                                <div className="relative">
                                    <Input
                                        data-testid="signup-password"
                                        type={showPw ? "text" : "password"}
                                        required
                                        value={password}
                                        onChange={(e) => setPassword(e.target.value)}
                                        className="h-11 rounded-none border-ink-line pr-10 focus-visible:border-gold focus-visible:ring-0"
                                    />
                                    <button
                                        type="button"
                                        onClick={() => setShowPw((v) => !v)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-muted"
                                    >
                                        {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                                    </button>
                                </div>
                            </div>
                            <div>
                                <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                    Confirm
                                </label>
                                <Input
                                    data-testid="signup-confirm"
                                    type={showPw ? "text" : "password"}
                                    required
                                    value={confirm}
                                    onChange={(e) => setConfirm(e.target.value)}
                                    className="h-11 rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0"
                                />
                            </div>
                        </div>

                        {password && (
                            <div data-testid="pw-strength">
                                <div className="flex gap-1">
                                    {Array.from({ length: 5 }).map((_, i) => (
                                        <div
                                            key={i}
                                            className={`h-1 flex-1 ${
                                                i < score
                                                    ? strengthColors[Math.min(score, 5)]
                                                    : "bg-ink-line"
                                            }`}
                                        />
                                    ))}
                                </div>
                                <div className="mt-1 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                    {strengthLabels[Math.min(score, 5)]}
                                </div>
                            </div>
                        )}

                        <label className="flex items-start gap-2 text-sm text-ink-muted">
                            <Checkbox
                                data-testid="signup-terms"
                                checked={agree}
                                onCheckedChange={(v) => setAgree(!!v)}
                                className="mt-1 rounded-none border-ink-line data-[state=checked]:bg-navy data-[state=checked]:text-gold"
                            />
                            <span>
                                I agree to the{" "}
                                <a className="text-navy underline">Terms</a> and{" "}
                                <a className="text-navy underline">Privacy Policy</a>.
                            </span>
                        </label>

                        {error && (
                            <div
                                data-testid="signup-error"
                                className="flex items-center gap-2 border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
                            >
                                <AlertCircle size={14} />
                                {error}
                            </div>
                        )}

                        <button
                            type="submit"
                            disabled={loading}
                            data-testid="signup-submit-btn"
                            className="btn-gold w-full"
                        >
                            {loading ? "Creating account…" : "Create account"}
                            <ArrowRight size={16} />
                        </button>
                    </form>

                    <p className="mt-8 text-center text-sm text-ink-muted">
                        Already have an account?{" "}
                        <Link
                            to="/login"
                            data-testid="goto-login-link"
                            className="font-semibold text-navy hover:text-gold"
                        >
                            Sign in →
                        </Link>
                    </p>
                </div>
            </div>
        </div>
    );
}
