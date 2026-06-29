import { useEffect, useState, useCallback } from "react";
import { api, errMessage } from "@/lib/api";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { AdminLayout } from "./AdminLayout";

export function AdminAuditLog() {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [filterAction, setFilterAction] = useState("");
    const [filterUser, setFilterUser] = useState("");

    const load = useCallback(async () => {
        setLoading(true);

        try {
            const params = new URLSearchParams({
                limit: "300",
            });

            if (filterAction) {
                params.set("action", filterAction);
            }

            if (filterUser) {
                params.set("user_id", filterUser);
            }

            const { data } = await api.get(
                `/api/admin/audit-log?${params.toString()}`
            );

            setItems(data.items || []);
        } catch (e) {
            toast.error(errMessage(e));
        } finally {
            setLoading(false);
        }
    }, [filterAction, filterUser]);

    useEffect(() => {
        load();
    }, [load]);

    return (
        <AdminLayout title="Audit log">
            <div className="card-steel">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-line px-6 py-4">
                    <div className="font-heading text-lg font-bold uppercase tracking-wide text-navy">
                        Audit events
                    </div>

                    <div className="flex items-center gap-2">
                        <Input
                            data-testid="audit-action-filter"
                            placeholder="action prefix (e.g. admin.user)"
                            value={filterAction}
                            onChange={(e) => setFilterAction(e.target.value)}
                            className="h-9 w-56 rounded-none border-ink-line font-mono text-[11px] uppercase tracking-wider focus-visible:border-gold focus-visible:ring-0"
                        />

                        <Input
                            data-testid="audit-user-filter"
                            placeholder="user id"
                            value={filterUser}
                            onChange={(e) => setFilterUser(e.target.value)}
                            className="h-9 w-48 rounded-none border-ink-line font-mono text-[11px] uppercase tracking-wider focus-visible:border-gold focus-visible:ring-0"
                        />

                        <button
                            onClick={load}
                            data-testid="audit-apply-filters"
                            className="btn-ghost-navy"
                        >
                            Apply
                        </button>
                    </div>
                </div>

                {loading ? (
                    <Skeleton className="m-6 h-64 rounded-none" />
                ) : items.length === 0 ? (
                    <div className="p-12 text-center text-sm text-ink-muted">
                        No audit events recorded.
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-left">
                            <thead className="bg-background">
                                <tr className="border-b border-ink-line">
                                    {[
                                        "Time",
                                        "User",
                                        "Action",
                                        "Resource",
                                        "Extra",
                                        "IP",
                                    ].map((h) => (
                                        <th
                                            key={h}
                                            className="px-4 py-3 font-mono text-[10px] uppercase tracking-wider text-ink-muted"
                                        >
                                            {h}
                                        </th>
                                    ))}
                                </tr>
                            </thead>

                            <tbody>
                                {items.map((a) => (
                                    <tr
                                        key={a.id}
                                        className="border-b border-ink-line last:border-b-0"
                                    >
                                        <td className="whitespace-nowrap px-4 py-3 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                            {new Date(
                                                a.timestamp
                                            ).toLocaleString()}
                                        </td>

                                        <td className="px-4 py-3 font-mono text-[10px] text-navy">
                                            {(a.user_id || "").slice(0, 12)}…
                                        </td>

                                        <td className="px-4 py-3 font-heading text-xs font-semibold uppercase tracking-wide text-navy">
                                            {a.action}
                                        </td>

                                        <td className="px-4 py-3 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                            {a.resource}{" "}
                                            {a.resource_id
                                                ? `· ${a.resource_id.slice(
                                                      0,
                                                      10
                                                  )}…`
                                                : ""}
                                        </td>

                                        <td className="px-4 py-3 font-mono text-[10px] text-ink-muted">
                                            {Object.keys(a.extra || {}).length
                                                ? JSON.stringify(a.extra)
                                                : "—"}
                                        </td>

                                        <td className="px-4 py-3 font-mono text-[10px] text-ink-muted">
                                            {a.ip_address || "—"}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </AdminLayout>
    );
}
