import { Fingerprint, ShieldCheck } from "lucide-react";

export function BlockchainBadge({ hash, verified = true, className = "" }) {
    if (!hash) return null;
    const short = `${hash.slice(0, 6)}…${hash.slice(-6)}`;
    return (
        <div
            data-testid="blockchain-badge"
            className={`inline-flex items-center gap-2 border border-gold/50 bg-gold-pale px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-navy ${className}`}
        >
            {verified ? (
                <ShieldCheck size={14} className="text-gold" />
            ) : (
                <Fingerprint size={14} className="text-gold" />
            )}
            <span className="font-semibold">SHA-256 ANCHORED</span>
            <span className="font-normal text-ink-muted">{short}</span>
        </div>
    );
}
