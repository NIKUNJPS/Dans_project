import { Link, useLocation } from "react-router-dom";

const ADMIN_TABS = [
    { to: "/admin/users", label: "Users" },
    { to: "/admin/permissions", label: "Permissions" },
    { to: "/admin/audit-log", label: "Audit Log" },
    { to: "/admin/analytics", label: "Analytics" },
];

export function AdminLayout({ children, title = "Platform control" }) {
    const loc = useLocation();
    return (
        <div className="min-h-full bg-background">
            <div className="border-b border-ink-line bg-white px-10 py-10">
                <div className="text-overline">Super Admin Console</div>
                <h1 className="mt-2 font-heading text-5xl font-black leading-none tracking-tight text-navy">
                    {title}
                </h1>
                <div className="mt-6 flex flex-wrap gap-6 border-b border-ink-line">
                    {ADMIN_TABS.map((t) => {
                        const active = loc.pathname.startsWith(t.to);
                        return (
                            <Link
                                key={t.to}
                                to={t.to}
                                data-testid={`admin-tab-${t.label.toLowerCase().replace(/\s/g, "-")}`}
                                className={`border-b-2 pb-3 font-heading text-sm uppercase tracking-wider transition-colors ${
                                    active
                                        ? "border-gold text-navy"
                                        : "border-transparent text-ink-muted hover:text-navy"
                                }`}
                            >
                                {t.label}
                            </Link>
                        );
                    })}
                </div>
            </div>
            <div className="container-steel py-10">{children}</div>
        </div>
    );
}

export function Kpi({ label, value, icon: Icon }) {
    return (
        <div className="card-steel flex items-center justify-between p-5">
            <div>
                <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                    {label}
                </div>
                <div className="mt-2 font-heading text-4xl font-black leading-none text-navy">
                    {value}
                </div>
            </div>
            <Icon size={20} className="text-gold" />
        </div>
    );
}
