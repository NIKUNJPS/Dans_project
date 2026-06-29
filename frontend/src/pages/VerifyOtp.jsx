import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { AlertCircle, ArrowLeft, Mail, RefreshCw } from "lucide-react";
import { Logo } from "@/components/brand/Logo";
import { OtpInput } from "@/components/brand/OtpInput";
import { api, errMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";

export default function VerifyOtp() {
    const [params] = useSearchParams();
    const userId = params.get("user_id");
    const email = params.get("email") || "";
    const [otp, setOtp] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const [countdown, setCountdown] = useState(60);
    const [attemptsLeft, setAttemptsLeft] = useState(5);
    const nav = useNavigate();
    const { login } = useAuth();

    useEffect(() => {
        if (countdown <= 0) return;
        const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
        return () => clearTimeout(t);
    }, [countdown]);

    const verify = async () => {
        setError("");
        if (otp.length !== 6) {
            setError("Enter the 6-digit code");
            return;
        }
        setLoading(true);
        try {
            const { data } = await api.post("/api/auth/verify-otp", {
                user_id: userId,
                otp,
            });
            login(
                { access_token: data.access_token, refresh_token: data.refresh_token },
                data.user,
            );
            toast.success("Email verified — welcome aboard!");
            nav("/dashboard");
        } catch (err) {
            setError(errMessage(err));
            setAttemptsLeft((a) => Math.max(0, a - 1));
            setOtp("");
        } finally {
            setLoading(false);
        }
    };

    const resend = async () => {
        setError("");
        try {
            await api.post("/api/auth/resend-otp", { user_id: userId });
            setCountdown(60);
            setAttemptsLeft(5);
            toast.success("A new 6-digit code is on its way.");
        } catch (err) {
            setError(errMessage(err));
        }
    };

    useEffect(() => {
        if (otp.length === 6 && !loading) {
            verify();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [otp]);

    return (
        <div
            data-testid="verify-otp-page"
            className="flex min-h-screen items-center justify-center bg-background px-4 py-12"
        >
            <div className="w-full max-w-md">
                <div className="mb-10 text-center">
                    <Logo variant="dark" size="lg" className="justify-center" />
                </div>

                <div className="card-steel p-8 md:p-10">
                    <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center border-2 border-gold bg-gold-pale">
                        <Mail size={26} className="text-navy" />
                    </div>
                    <h1 className="text-center font-heading text-3xl font-black uppercase tracking-tight text-navy">
                        Check your email
                    </h1>
                    <p className="mt-2 text-center text-sm text-ink-muted">
                        We sent a 6-digit code to
                    </p>
                    <p className="mt-1 text-center font-mono text-sm font-semibold text-navy">
                        {email}
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
                        <div
                            data-testid="otp-error"
                            className="mt-4 flex items-center gap-2 border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
                        >
                            <AlertCircle size={14} />
                            {error}
                        </div>
                    )}

                    <div className="mt-6 text-center font-mono text-[10px] uppercase tracking-[0.22em] text-ink-muted">
                        {attemptsLeft} attempts remaining
                    </div>

                    <button
                        type="button"
                        onClick={verify}
                        disabled={loading || otp.length !== 6}
                        data-testid="verify-otp-btn"
                        className="btn-navy mt-6 w-full"
                    >
                        {loading ? "Verifying…" : "Verify & continue"}
                    </button>

                    <div className="mt-6 flex flex-col items-center gap-2">
                        {countdown > 0 ? (
                            <div className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">
                                Resend code in 0:{countdown.toString().padStart(2, "0")}
                            </div>
                        ) : (
                            <button
                                onClick={resend}
                                data-testid="resend-otp-btn"
                                className="inline-flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-gold hover:text-navy"
                            >
                                <RefreshCw size={12} />
                                Resend code
                            </button>
                        )}
                    </div>
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
