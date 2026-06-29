import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import {
    ArrowRight,
    Boxes,
    Calculator,
    CircleDot,
    FolderKanban,
    MessageSquareQuote,
    Sparkles,
    TrendingUp,
} from "lucide-react";
import {
    Area,
    AreaChart,
    Cell,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { roleLabel } from "@/components/brand/RolePillSelector";
import { Skeleton } from "@/components/ui/skeleton";

const COLORS = ["#f5a800", "#0d2240", "#1a3a5c", "#ffd166", "#6b8299", "#16a34a", "#ef4444"];

function StatCard({ label, value, trend, icon: Icon, testId, delay = 0 }) {
    return (
        <motion.div
            data-testid={testId}
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay, ease: [0.22, 1, 0.36, 1] }}
            whileHover={{ y: -4 }}
            className="card-steel relative flex flex-col justify-between p-6 hover-lift"
        >
            <div className="absolute left-0 top-0 h-[3px] w-full origin-left scale-x-0 bg-gold transition-transform duration-500 group-hover:scale-x-100" />
            <div className="flex items-start justify-between">
                <div>
                    <div className="font-mono text-[11px] uppercase tracking-[0.24em] text-ink-muted">
                        {label}
                    </div>
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ duration: 0.6, delay: delay + 0.15 }}
                        className="mt-3 font-heading text-5xl font-black leading-none text-navy"
                    >
                        {value}
                    </motion.div>
                </div>
                <div className="border border-ink-line p-2">
                    <Icon size={18} className="text-gold" />
                </div>
            </div>
            {trend && (
                <div className="mt-4 inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-success">
                    <TrendingUp size={12} />
                    {trend}
                </div>
            )}
        </motion.div>
    );
}

export default function Dashboard() {
    const { user } = useAuth();
    const [data, setData] = useState(null);
    const [usage, setUsage] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const [s, u] = await Promise.all([
                    api.get("/api/dashboard/stats"),
                    api.get("/api/usage/me"),
                ]);
                setData(s.data);
                setUsage(u.data);
            } catch {}
            setLoading(false);
        })();
    }, []);

    const usageLabel = usage
        ? usage.analyses_limit === -1
            ? `${usage.analyses_used} analyses (unlimited)`
            : `${usage.analyses_used} / ${usage.analyses_limit} analyses used`
        : "—";

    return (
        <div className="min-h-full bg-background">
            <div className="border-b border-ink-line bg-white px-10 py-10">
                <div className="flex items-end justify-between">
                    <div>
                        <div className="text-overline">Welcome back</div>
                        <h1 className="mt-2 font-heading text-5xl font-black leading-none tracking-tight text-navy">
                            {user?.first_name} {user?.last_name}
                        </h1>
                        <div className="mt-3 flex items-center gap-3 font-mono text-xs uppercase tracking-wider text-ink-muted">
                            <span className="inline-flex items-center gap-1.5">
                                <CircleDot size={10} className="text-gold" />
                                Role · {roleLabel(user?.role)}
                            </span>
                            <span>·</span>
                            <span>{usageLabel}</span>
                        </div>
                    </div>
                    <div className="flex gap-3">
                        <Link to="/analyze" className="btn-gold" data-testid="dashboard-quick-analyze">
                            <Sparkles size={14} />
                            Quick Analyze
                            <ArrowRight size={14} />
                        </Link>
                    </div>
                </div>
            </div>

            {/* CONTENT */}
            <div className="container-steel py-10">
                {/* STATS */}
                <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
                    {loading
                        ? Array.from({ length: 4 }).map((_, i) => (
                              <Skeleton
                                  key={i}
                                  className="h-36 rounded-none border border-ink-line"
                              />
                          ))
                        : [
                              {
                                  label: "TOTAL PROJECTS",
                                  value: data?.stats?.total_projects ?? 0,
                                  icon: FolderKanban,
                                  testId: "stat-projects",
                              },
                              {
                                  label: "ACTIVE ANALYSES",
                                  value: data?.stats?.active_analyses ?? 0,
                                  icon: Sparkles,
                                  testId: "stat-active-analyses",
                              },
                              {
                                  label: "OPEN RFIs",
                                  value: data?.stats?.open_rfis ?? 0,
                                  icon: MessageSquareQuote,
                                  testId: "stat-open-rfis",
                              },
                              {
                                  label: "FILES PROCESSED",
                                  value: data?.stats?.files_processed ?? 0,
                                  icon: Boxes,
                                  testId: "stat-files",
                              },
                          ].map((s) => <StatCard key={s.label} {...s} />)}
                </div>

                {/* CHARTS */}
                <div className="mt-8 grid gap-6 md:grid-cols-5">
                    <div className="card-steel p-6 md:col-span-3">
                        <div className="flex items-center justify-between">
                            <div>
                                <div className="text-overline">Activity · 30 days</div>
                                <div className="mt-1 font-heading text-2xl font-bold uppercase tracking-tight text-navy">
                                    Analyses over time
                                </div>
                            </div>
                        </div>
                        <div className="mt-6 h-64">
                            <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
                                <AreaChart data={data?.activity || []}>
                                    <defs>
                                        <linearGradient id="gold" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="0%" stopColor="#f5a800" stopOpacity={0.4} />
                                            <stop offset="100%" stopColor="#f5a800" stopOpacity={0} />
                                        </linearGradient>
                                    </defs>
                                    <XAxis
                                        dataKey="date"
                                        stroke="#6b8299"
                                        tick={{ fontSize: 10, fontFamily: "JetBrains Mono" }}
                                        tickFormatter={(d) => d.slice(8, 10)}
                                        axisLine={{ stroke: "#e2eaf2" }}
                                        tickLine={false}
                                    />
                                    <YAxis
                                        stroke="#6b8299"
                                        tick={{ fontSize: 10, fontFamily: "JetBrains Mono" }}
                                        axisLine={{ stroke: "#e2eaf2" }}
                                        tickLine={false}
                                        allowDecimals={false}
                                    />
                                    <Tooltip
                                        contentStyle={{
                                            background: "#0d2240",
                                            border: "1px solid #f5a800",
                                            color: "white",
                                            borderRadius: 0,
                                            fontFamily: "JetBrains Mono",
                                        }}
                                        labelStyle={{ color: "#f5a800" }}
                                    />
                                    <Area
                                        type="monotone"
                                        dataKey="analyses"
                                        stroke="#f5a800"
                                        strokeWidth={2}
                                        fill="url(#gold)"
                                    />
                                </AreaChart>
                            </ResponsiveContainer>
                        </div>
                    </div>

                    <div className="card-steel p-6 md:col-span-2">
                        <div className="text-overline">Mode usage</div>
                        <div className="mt-1 font-heading text-2xl font-bold uppercase tracking-tight text-navy">
                            Top modes
                        </div>
                        <div className="mt-4 h-64">
                            {data?.mode_usage?.length ? (
                                <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
                                    <PieChart>
                                        <Pie
                                            data={data.mode_usage}
                                            dataKey="count"
                                            nameKey="mode"
                                            innerRadius={50}
                                            outerRadius={90}
                                            stroke="#ffffff"
                                            strokeWidth={2}
                                        >
                                            {data.mode_usage.map((_, i) => (
                                                <Cell
                                                    key={i}
                                                    fill={COLORS[i % COLORS.length]}
                                                />
                                            ))}
                                        </Pie>
                                        <Tooltip
                                            contentStyle={{
                                                background: "#0d2240",
                                                border: "1px solid #f5a800",
                                                color: "white",
                                                borderRadius: 0,
                                                fontFamily: "JetBrains Mono",
                                            }}
                                        />
                                    </PieChart>
                                </ResponsiveContainer>
                            ) : (
                                <div className="flex h-full items-center justify-center text-sm text-ink-muted">
                                    Run your first analysis to see mode usage.
                                </div>
                            )}
                        </div>
                    </div>
                </div>

                {/* RECENT */}
                <div className="mt-8 card-steel">
                    <div className="flex items-center justify-between border-b border-ink-line px-6 py-4">
                        <div className="font-heading text-lg font-bold uppercase tracking-wide text-navy">
                            Recent analyses
                        </div>
                        <Link
                            to="/outputs"
                            data-testid="view-all-outputs"
                            className="font-mono text-[10px] uppercase tracking-wider text-gold hover:text-navy"
                        >
                            View all →
                        </Link>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="w-full text-left">
                            <thead className="bg-background">
                                <tr className="border-b border-ink-line">
                                    {["Mode", "Status", ""].map((h) => (
                                        <th
                                            key={h}
                                            className="px-6 py-3 font-mono text-[10px] uppercase tracking-wider text-ink-muted"
                                        >
                                            {h}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {(data?.recent_analyses || []).map((a) => (
                                    <tr
                                        key={a.id}
                                        data-testid={`recent-analysis-${a.id}`}
                                        className="border-b border-ink-line last:border-b-0"
                                    >
                                        <td className="px-6 py-4">
                                            <div className="font-heading text-sm font-semibold uppercase text-navy">
                                                {a.mode_label || a.mode}
                                            </div>
                                            <div className="font-mono text-[10px] uppercase text-ink-muted">
                                                {new Date(a.created_at).toLocaleString()}
                                            </div>
                                        </td>
                                        <td className="px-6 py-4">
                                            <StatusPill status={a.status} />
                                        </td>
                                        <td className="px-6 py-4">
                                            <Link
                                                to={`/analyses/${a.id}`}
                                                className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-gold hover:text-navy"
                                            >
                                                View <ArrowRight size={12} />
                                            </Link>
                                        </td>
                                    </tr>
                                ))}
                                {(!data?.recent_analyses || data.recent_analyses.length === 0) && (
                                    <tr>
                                        <td
                                            colSpan={3}
                                            className="px-6 py-12 text-center text-sm text-ink-muted"
                                        >
                                            No analyses yet — <Link className="text-gold underline" to="/analyze">run your first one</Link>.
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    );
}

function StatusPill({ status }) {
    const map = {
        queued: { text: "Queued", cls: "border-ink-line text-ink-muted" },
        processing: { text: "Processing", cls: "border-gold bg-gold-pale text-navy" },
        complete: { text: "Complete", cls: "border-success text-success" },
        failed: { text: "Failed", cls: "border-destructive text-destructive" },
    };
    const cfg = map[status] || map.queued;
    return (
        <span
            className={`inline-flex items-center gap-1 border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${cfg.cls}`}
        >
            {status === "processing" && (
                <span className="h-1.5 w-1.5 animate-pulse-ring rounded-full bg-gold" />
            )}
            {cfg.text}
        </span>
    );
}