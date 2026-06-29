import { Hexagon } from "lucide-react";

export function Logo({ variant = "dark", size = "md", className = "" }) {
    const isLight = variant === "light";
    const sizes = {
        sm: { hex: 28, title: "text-sm", sub: "text-[10px]" },
        md: { hex: 36, title: "text-base", sub: "text-xs" },
        lg: { hex: 56, title: "text-2xl", sub: "text-sm" },
    };
    const s = sizes[size] || sizes.md;
    return (
        <div
            data-testid="brand-logo"
            className={`inline-flex items-center gap-3 ${className}`}
        >
            <div className="relative inline-flex items-center justify-center">
                <Hexagon
                    size={s.hex}
                    strokeWidth={1.5}
                    className={isLight ? "text-white" : "text-navy"}
                    fill={isLight ? "#0d2240" : "#0d2240"}
                />
                <span
                    className="absolute font-heading font-black leading-none text-white"
                    style={{ fontSize: s.hex * 0.38 }}
                >
                    4X
                </span>
            </div>
            <div className="flex flex-col leading-tight">
                <span
                    className={`font-heading font-extrabold uppercase tracking-[0.18em] ${
                        isLight ? "text-white" : "text-navy"
                    } ${s.title}`}
                >
                    STRUCT<span className="text-gold">MIND</span>
                </span>
                <span
                    className={`font-mono font-medium uppercase tracking-[0.28em] ${
                        isLight ? "text-white/60" : "text-ink-muted"
                    } ${s.sub}`}
                >
                    4XStruct-INC
                </span>
            </div>
        </div>
    );
}

export default Logo;
