import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export function OtpInput({
    length = 6,
    value,
    onChange,
    error = false,
    disabled = false,
    autoFocus = true,
}) {
    const [digits, setDigits] = useState(
        Array.from({ length }, (_, i) => value?.[i] || ""),
    );
    const refs = useRef([]);

    useEffect(() => {
        const next = Array.from({ length }, (_, i) => value?.[i] || "");
        setDigits(next);
    }, [value, length]);

    useEffect(() => {
        if (autoFocus) {
            refs.current[0]?.focus();
        }
    }, [autoFocus]);

    const update = (next) => {
        setDigits(next);
        onChange(next.join(""));
    };

    const handleChange = (i, raw) => {
        const v = raw.replace(/\D/g, "").slice(-1);
        const next = [...digits];
        next[i] = v;
        update(next);
        if (v && i < length - 1) refs.current[i + 1]?.focus();
    };

    const handleKeyDown = (i, e) => {
        if (e.key === "Backspace" && !digits[i] && i > 0) {
            refs.current[i - 1]?.focus();
        }
        if (e.key === "ArrowLeft" && i > 0) refs.current[i - 1]?.focus();
        if (e.key === "ArrowRight" && i < length - 1) refs.current[i + 1]?.focus();
    };

    const handlePaste = (e) => {
        const text = (e.clipboardData.getData("text") || "")
            .replace(/\D/g, "")
            .slice(0, length);
        if (!text) return;
        e.preventDefault();
        const next = Array.from({ length }, (_, i) => text[i] || "");
        update(next);
        refs.current[Math.min(text.length, length - 1)]?.focus();
    };

    return (
        <div
            data-testid="otp-input"
            className={cn(
                "flex items-center justify-center gap-2 md:gap-3",
                error && "animate-shake",
            )}
        >
            {digits.map((d, i) => (
                <input
                    key={i}
                    ref={(el) => (refs.current[i] = el)}
                    data-testid={`otp-digit-${i}`}
                    value={d}
                    disabled={disabled}
                    onChange={(e) => handleChange(i, e.target.value)}
                    onKeyDown={(e) => handleKeyDown(i, e)}
                    onPaste={handlePaste}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={1}
                    className={cn(
                        "h-14 w-12 md:h-16 md:w-14 border-2 bg-white text-center font-mono text-2xl font-bold tracking-tight text-navy outline-none transition-all",
                        error
                            ? "border-destructive"
                            : d
                              ? "border-navy"
                              : "border-ink-line focus:border-gold",
                    )}
                />
            ))}
        </div>
    );
}
