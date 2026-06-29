import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Skeleton } from "@/components/ui/skeleton";
import { Shield, AlertOctagon, MessageSquareQuote, CheckCircle2 } from "lucide-react";

export default function RiskDashboard() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const [a, r] = await Promise.all([
                    api.get("/api/analyses?limit=100"),
                    api.get("/api/rfis"),
                ]);
                const analyses = a.data.items;
                const rfis = r.data.items;

                const crit = analyses.reduce((s, x) => s + (x.issues_found?.critical || 0), 0);
                const major = analyses.reduce((s, x) => s + (x.issues_found?.major || 0), 0);
                const minor = analyses.reduce((s, x) => s + (x.issues_found?.minor || 0), 0);

                const blocking = rfis.filter((x) => x.blocking).length;

                setData({
                    analyses,
                    rfis,
                    crit,
                    major,
                    minor,
                    blocking,
                    avgScore:
                        analyses.length > 0
                            ? analyses.reduce((s, x) => s + (x.quality_score || 0), 0) /
                              analyses.length
                            : 0,
                });
            } catch {}
            setLoading(false);
        })();
    }, []);

    if (loading) return <Skeleton className="m-10 h-96 rounded-none" />;
    if (!data) return null;

    return (
        <div className="min-h-full bg-background">
            <div className="border-b border-ink-line bg-white px-10 py-10">
                <div className="text-overline">Risk Console</div>
                <h1 className="mt-2 font-heading text-5xl font-black leading-none tracking-tight text-navy">
                    Portfolio risk
                </h1>
            </div>

            <div className="container-steel py-10">
                <div className="grid gap-6 md:grid-cols-4">
                    <Kpi label="CRITICAL ISSUES" value={data.crit} color="text-destructive" icon={AlertOctagon} />
                    <Kpi label="MAJOR ISSUES" value={data.major} color="text-warning" icon={AlertOctagon} />
                    <Kpi label="BLOCKING RFIs" value={data.blocking} color="text-destructive" icon={MessageSquareQuote} />
                    <Kpi label="AVG QC SCORE" value={`${Math.round(data.avgScore)}%`} color="text-navy" icon={Shield} />
                </div>

                <div className="mt-8 card-steel">
                    <div className="border-b border-ink-line px-6 py-4">
                        <div className="font-heading text-lg font-bold uppercase tracking-wide text-navy">
                            Recent critical issues
                        </div>
                    </div>
                    {data.analyses.filter((a) => a.issues_found?.critical > 0).length === 0 ? (
                        <div className="p-12 text-center">
                            <CheckCircle2 size={48} className="mx-auto text-success" />
                            <div className="mt-4 font-heading text-xl font-bold uppercase tracking-tight text-navy">
                                No critical issues. All clear.
                            </div>
                        </div>
                    ) : (
                        <div className="divide-y divide-ink-line">
                            {data.analyses
                                .filter((a) => a.issues_found?.critical > 0)
                                .slice(0, 10)
                                .map((a) => (
                                    <div
                                        key={a.id}
                                        className="flex items-center justify-between px-6 py-4"
                                    >
                                        <div>
                                            <div className="font-heading text-sm font-semibold uppercase tracking-wide text-navy">
                                                {a.mode_label || a.mode}
                                            </div>
                                            <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                                {new Date(a.created_at).toLocaleString()}
                                            </div>
                                        </div>
                                        <div className="flex gap-2 font-mono text-xs uppercase tracking-wider">
                                            <span className="border border-destructive px-2 py-0.5 text-destructive">
                                                {a.issues_found?.critical} critical
                                            </span>
                                            <span className="border border-warning px-2 py-0.5 text-warning">
                                                {a.issues_found?.major} major
                                            </span>
                                        </div>
                                    </div>
                                ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function Kpi({ label, value, color, icon: Icon }) {
    return (
        <div className="card-steel flex items-start justify-between p-5">
            <div>
                <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                    {label}
                </div>
                <div className={`mt-2 font-heading text-4xl font-black leading-none ${color}`}>
                    {value}
                </div>
            </div>
            <Icon size={20} className={color} />
        </div>
    );
}
