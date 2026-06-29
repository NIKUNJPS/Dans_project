import { Link, useNavigate } from "react-router-dom";
import { useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowRight, Eye, EyeOff, Mail } from "lucide-react";
import { Logo } from "@/components/brand/Logo";
import { OtpInput } from "@/components/brand/OtpInput";
import { api, errMessage } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";

export default function ForgotPassword() {
    const [step, setStep] = useState(1);
    const [email, setEmail] = useState("");
    const [resetToken, setResetToken] = useState("");
    const [otp, setOtp] = useState("");
    const [sessionToken, setSessionToken] = useState("");
    const [newPw, setNewPw] = useState("");
    const [confirmPw, setConfirmPw] = useState("");
    const [showPw, setShowPw] = useState(false);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const [countdown, setCountdown] = useState(60);
    const nav = useNavigate();

    useEffect(() => {
        if (step === 2 && countdown > 0) {
            const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
            return () => clearTimeout(t);
        }
    }, [countdown, step]);

    const sendCode = async (e) => {
        e?.preventDefault();
        setError("");
        setLoading(true);
        try {
            const { data } = await api.post("/api/auth/forgot-password", { email });
            setResetToken(data.reset_token || "");
            setStep(2);
            setCountdown(60);
            toast.success("If that email exists, a reset code has been sent.");
        } catch (err) {
            setError(errMessage(err));
        } finally {
            setLoading(false);
        }
    };

    const verifyCode = async () => {
        setError("");
        if (otp.length !== 6) {
            setError("Enter the 6-digit code");
            return;
        }
        setLoading(true);
        try {
            const { data } = await api.post("/api/auth/verify-reset-otp", {
                reset_token: resetToken,
                otp,
            });
            setSessionToken(data.reset_session_token);
            setStep(3);
        } catch (err) {
            setError(errMessage(err));
            setOtp("");
        } finally {
            setLoading(false);
        }
    };

    const changePassword = async (e) => {
        e.preventDefault();
        setError("");
        if (newPw !== confirmPw) {
            setError("Passwords do not match");
            return;
        }
        setLoading(true);
        try {
            await api.post("/api/auth/reset-password", {
                reset_session_token: sessionToken,
                new_password: newPw,
            });
            toast.success("Password reset — you can now sign in.");
            nav("/login");
        } catch (err) {
            setError(errMessage(err));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (step === 2 && otp.length === 6 && !loading) {
            verifyCode();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [otp, step]);

    return (
        <div
            data-testid="forgot-password-page"
            className="flex min-h-screen items-center justify-center bg-background px-4 py-12"
        >
            <div className="w-full max-w-md">
                <div className="mb-8 text-center">
                    <Logo variant="dark" size="lg" className="justify-center" />
                </div>
                <div className="card-steel p-8 md:p-10">
                    {step === 1 && (
                        <>
                            <h1 className="font-heading text-3xl font-black uppercase tracking-tight text-navy">
                                Reset your password
                            </h1>
                            <p className="mt-2 text-sm text-ink-muted">
                                We'll email you a 6-digit code to confirm.
                            </p>
                            <form
                                onSubmit={sendCode}
                                className="mt-6 space-y-4"
                                data-testid="forgot-email-form"
                            >
                                <div>
                                    <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-muted">
                                        Email
                                    </label>
                                    <div className="relative">
                                        <Mail
                                            size={14}
                                            className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted"
                                        />
                                        <Input
                                            data-testid="forgot-email-input"
                                            type="email"
                                            required
                                            value={email}
                                            onChange={(e) => setEmail(e.target.value)}
                                            className="h-12 rounded-none border-ink-line pl-9 focus-visible:border-gold focus-visible:ring-0"
                                        />
                                    </div>
                                </div>
                                {error && (
                                    <div className="flex items-center gap-2 border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                                        <AlertCircle size={14} />
                                        {error}
                                    </div>
                                )}
                                <button
                                    type="submit"
                                    disabled={loading}
                                    data-testid="forgot-send-btn"
                                    className="btn-navy w-full"
                                >
                                    {loading ? "Sending…" : "Send reset code"}
                                    <ArrowRight size={16} />
                                </button>
                            </form>
                        </>
                    )}

                    {step === 2 && (
                        <>
                            <h1 className="font-heading text-3xl font-black uppercase tracking-tight text-navy">
                                Enter reset code
                            </h1>
                            <p className="mt-2 text-sm text-ink-muted">
                                Sent to <span className="font-mono text-navy">{email}</span>
                            </p>
                            <div className="mt-8">
                                <OtpInput
                                    length={6}
                                    value={otp}
                                    onChange={setOtp}
                                    error={!!error}
                                    disabled={loading}
                                />
                            </div>
                            {error && (
                                <div className="mt-4 flex items-center gap-2 border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                                    <AlertCircle size={14} />
                                    {error}
                                </div>
                            )}
                            <button
                                type="button"
                                onClick={verifyCode}
                                disabled={loading || otp.length !== 6}
                                data-testid="forgot-verify-btn"
                                className="btn-navy mt-6 w-full"
                            >
                                {loading ? "Verifying…" : "Verify code"}
                            </button>
                            <div className="mt-4 text-center font-mono text-[11px] uppercase tracking-wider text-ink-muted">
                                {countdown > 0 ? (
                                    `Code expires in 0:${countdown.toString().padStart(2, "0")}`
                                ) : (
                                    <button
                                        onClick={sendCode}
                                        className="text-gold hover:text-navy"
                                    >
                                        Resend code
                                    </button>
                                )}
                            </div>
                        </>
                    )}

                    {step === 3 && (
                        <>
                            <h1 className="font-heading text-3xl font-black uppercase tracking-tight text-navy">
                                New password
                            </h1>
                            <p className="mt-2 text-sm text-ink-muted">
                                Min 8 chars · 1 upper · 1 number · 1 special.
                            </p>
                            <form
                                onSubmit={changePassword}
                                className="mt-6 space-y-4"
                                data-testid="reset-password-form"
                            >
                                <div className="relative">
                                    <Input
                                        data-testid="reset-new-password"
                                        type={showPw ? "text" : "password"}
                                        placeholder="New password"
                                        required
                                        value={newPw}
                                        onChange={(e) => setNewPw(e.target.value)}
                                        className="h-12 rounded-none border-ink-line pr-10 focus-visible:border-gold focus-visible:ring-0"
                                    />
                                    <button
                                        type="button"
                                        onClick={() => setShowPw((v) => !v)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-muted"
                                    >
                                        {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                                    </button>
                                </div>
                                <Input
                                    data-testid="reset-confirm-password"
                                    type={showPw ? "text" : "password"}
                                    placeholder="Confirm new password"
                                    required
                                    value={confirmPw}
                                    onChange={(e) => setConfirmPw(e.target.value)}
                                    className="h-12 rounded-none border-ink-line focus-visible:border-gold focus-visible:ring-0"
                                />
                                {error && (
                                    <div className="flex items-center gap-2 border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                                        <AlertCircle size={14} />
                                        {error}
                                    </div>
                                )}
                                <button
                                    type="submit"
                                    disabled={loading}
                                    data-testid="reset-submit-btn"
                                    className="btn-gold w-full"
                                >
                                    {loading ? "Updating…" : "Reset password"}
                                </button>
                            </form>
                        </>
                    )}
                </div>
                <Link
                    to="/login"
                    className="mt-8 flex items-center justify-center gap-2 font-mono text-[11px] uppercase tracking-wider text-ink-muted hover:text-navy"
                >
                    <ArrowLeft size={12} />
                    Back to sign in
                </Link>
            </div>
        </div>
    );
}
