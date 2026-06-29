import { useEffect, useState } from "react";
import { Activity, Boxes, CheckCircle2, FileBarChart2, Layers, MessageSquareQuote, ShieldCheck, Users } from "lucide-react";
import {
    Bar,
    BarChart,
    Cell,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { AdminLayout, Kpi } from "./AdminLayout";

const COLORS = ["#f5a800", "#0d2240", "#1a3a5c", "#ffd166", "#6b8299", "#16a34a", "#ef4444"];

export function AdminAnalytics() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/api/admin/analytics");
                setData(data);
            } catch (e) {
                toast.error(errMessage(e));
            }
            setLoading(false);
        })();
    }, []);

    if (loading) {
        return (
            <AdminLayout title="Analytics">
                <Skeleton className="h-64 rounded-none" />
            </AdminLayout>
        );
    }

    return (
        <AdminLayout title="Analytics">
            <div className="grid gap-6 md:grid-cols-4">
                <Kpi label="USERS"     value={data?.total_users || 0}      icon={Users} />
                <Kpi label="ACTIVE"    value={data?.active_users || 0}     icon={CheckCircle2} />
                <Kpi label="PROJECTS"  value={data?.total_projects || 0}   icon={Layers} />
                <Kpi label="ANALYSES"  value={data?.total_analyses || 0}   icon={Activity} />
                <Kpi label="COMPLETE"  value={data?.complete_analyses || 0} icon={ShieldCheck} />
                <Kpi label="FAILED"    value={data?.failed_analyses || 0}  icon={FileBarChart2} />
                <Kpi label="FILES"     value={data?.total_files || 0}      icon={Boxes} />
                <Kpi label="RFIs"      value={data?.total_rfis || 0}       icon={MessageSquareQuote} />
            </div>

            <div className="mt-8 grid gap-6 md:grid-cols-2">
                <div className="card-steel p-6">
                    <div className="text-overline">Users by role</div>
                    <div className="mt-1 font-heading text-2xl font-bold uppercase tracking-tight text-navy">
                        Distribution
                    </div>
                    <div className="mt-4 h-64">
                        {data?.users_by_role?.length ? (
                            <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
                                <PieChart>
                                    <Pie
                                        data={data.users_by_role}
                                        dataKey="count"
                                        nameKey="role"
                                        innerRadius={50}
                                        outerRadius={90}
                                        stroke="#ffffff"
                                        strokeWidth={2}
                                        label={(e) => e.role}
                                    >
                                        {data.users_by_role.map((_, i) => (
                                            <Cell key={i} fill={COLORS[i % COLORS.length]} />
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
                                No data
                            </div>
                        )}
                    </div>
                </div>

                <div className="card-steel p-6">
                    <div className="text-overline">Top modes · this month</div>
                    <div className="mt-1 font-heading text-2xl font-bold uppercase tracking-tight text-navy">
                        Most-used modes
                    </div>
                    <div className="mt-4 h-64">
                        {data?.top_modes_this_month?.length ? (
                            <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
                                <BarChart data={data.top_modes_this_month}>
                                    <XAxis
                                        dataKey="mode"
                                        stroke="#6b8299"
                                        tick={{ fontSize: 9, fontFamily: "JetBrains Mono" }}
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
                                    />
                                    <Bar dataKey="count" fill="#f5a800" />
                                </BarChart>
                            </ResponsiveContainer>
                        ) : (
                            <div className="flex h-full items-center justify-center text-sm text-ink-muted">
                                No analyses this month yet.
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </AdminLayout>
    );
}
