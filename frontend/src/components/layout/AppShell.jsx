import { Link, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import {
    Bell,
    Calculator,
    Download,
    FolderKanban,
    GaugeCircle,
    LayoutDashboard,
    Lock,
    LogOut,
    MessageSquareQuote,
    Search,
    Settings,
    ShieldCheck,
    Sparkles,
    SlidersHorizontal,
    Users,
} from "lucide-react";
import { Logo } from "@/components/brand/Logo";
import { useAuth } from "@/context/AuthContext";
import { roleLabel } from "@/components/brand/RolePillSelector";
import { api } from "@/lib/api";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Input } from "@/components/ui/input";

const NAV = [
    { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
    { to: "/projects", label: "Projects", icon: FolderKanban },
    { to: "/analyze", label: "Analyze", icon: Sparkles, accent: true },
    { to: "/rfi-tracker", label: "RFI Tracker", icon: MessageSquareQuote },
    { to: "/outputs", label: "Outputs", icon: Download },
];

const ADMIN_NAV = [
    { to: "/admin/users", label: "Users", icon: Users },
    { to: "/admin/permissions", label: "Permissions", icon: SlidersHorizontal },
    { to: "/admin/audit-log", label: "Audit Log", icon: GaugeCircle },
    { to: "/admin/analytics", label: "Analytics", icon: ShieldCheck },
];

export function AppShell({ children }) {
    const { user, logout, impersonation, stopImpersonation } = useAuth();
    const nav = useNavigate();
    const loc = useLocation();
    const [notifications, setNotifications] = useState({ items: [], unread: 0 });

    const loadNotifications = async () => {
        try {
            const { data } = await api.get("/api/notifications");
            setNotifications(data);
        } catch {}
    };

    useEffect(() => {
        loadNotifications();
        const t = setInterval(loadNotifications, 15000);
        return () => clearInterval(t);
    }, []);

    const markAllRead = async () => {
        await api.put("/api/notifications/read-all");
        loadNotifications();
    };

    const initials = `${user?.first_name?.[0] || ""}${user?.last_name?.[0] || ""}`.toUpperCase() || "SM";
    const isSuperAdmin = user?.role === "super_admin";

    return (
        <div className="flex h-screen overflow-hidden bg-background text-ink">
            {/* IMPERSONATION BANNER */}
            {impersonation && (
                <div
                    data-testid="impersonation-banner"
                    className="absolute left-0 right-0 top-0 z-50 flex items-center justify-between bg-destructive px-6 py-2 font-mono text-[11px] uppercase tracking-wider text-white"
                >
                    <div className="flex items-center gap-2">
                        <Lock size={12} />
                        Impersonating <span className="text-gold">{impersonation.email}</span> · read-only · {impersonation.expiresInMinutes || 15} min
                    </div>
                    <button
                        data-testid="stop-impersonation"
                        onClick={() => {
                            stopImpersonation();
                            nav("/admin/users");
                        }}
                        className="border border-white px-2 py-0.5 hover:bg-white hover:text-destructive"
                    >
                        Exit impersonation
                    </button>
                </div>
            )}

            {/* SIDEBAR */}
            <aside
                data-testid="app-sidebar"
                className={`flex w-[260px] flex-shrink-0 flex-col bg-navy text-white ${impersonation ? "mt-8" : ""}`}
            >
                <div className="px-6 pt-8 pb-6">
                    <Logo variant="light" size="md" />
                </div>

                <div className="mx-4 my-2 border border-white/10 bg-white/5 p-3">
                    <div className="flex items-center gap-3">
                        <Avatar className="h-10 w-10 rounded-none border border-gold/40 bg-white/10">
                            <AvatarFallback className="rounded-none bg-navy-mid font-heading text-sm font-bold text-gold">
                                {initials}
                            </AvatarFallback>
                        </Avatar>
                        <div className="min-w-0 flex-1">
                            <div className="truncate font-heading text-sm font-semibold uppercase tracking-wide">
                                {user?.first_name} {user?.last_name}
                            </div>
                            <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em] text-gold">
                                <span className="inline-block h-1.5 w-1.5 rounded-full bg-gold" />
                                {roleLabel(user?.role)}
                            </div>
                        </div>
                    </div>
                </div>

                <nav className="mt-4 flex-1 px-3">
                    <div className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.28em] text-white/40">
                        Platform
                    </div>
                    {NAV.map((n) => {
                        const Icon = n.icon;
                        const active =
                            loc.pathname === n.to ||
                            (n.to !== "/dashboard" && loc.pathname.startsWith(n.to));
                        return (
                            <Link
                                key={n.to}
                                to={n.to}
                                data-testid={`nav-${n.to.slice(1)}`}
                                className={`group mb-1 flex items-center gap-3 border-l-2 px-4 py-2.5 font-heading text-sm uppercase tracking-wide transition-all ${
                                    active
                                        ? "border-gold bg-white/5 text-gold"
                                        : "border-transparent text-white/70 hover:border-white/20 hover:text-white"
                                } ${n.accent && !active ? "text-gold/90" : ""}`}
                            >
                                <Icon size={16} strokeWidth={1.8} />
                                {n.label}
                            </Link>
                        );
                    })}
                    {isSuperAdmin && (
                        <>
                            <div className="mt-6 px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.28em] text-white/40">
                                Super Admin
                            </div>
                            {ADMIN_NAV.map((n) => {
                                const Icon = n.icon;
                                const active = loc.pathname.startsWith(n.to);
                                return (
                                    <Link
                                        key={n.to}
                                        to={n.to}
                                        data-testid={`nav-admin-${n.label.toLowerCase().replace(/\s/g, "-")}`}
                                        className={`mb-1 flex items-center gap-3 border-l-2 px-4 py-2.5 font-heading text-sm uppercase tracking-wide transition-all ${
                                            active
                                                ? "border-gold bg-white/5 text-gold"
                                                : "border-transparent text-white/70 hover:text-white"
                                        }`}
                                    >
                                        <Icon size={16} strokeWidth={1.8} />
                                        {n.label}
                                    </Link>
                                );
                            })}
                        </>
                    )}
                </nav>

                <div className="px-4 pb-5 pt-4 border-t border-white/10">
                    <div className="flex items-center gap-2 pb-3 font-mono text-[10px] uppercase tracking-[0.24em] text-white/50">
                        <span className="h-2 w-2 animate-pulse-ring rounded-full bg-green-400" />
                        AISC 360-22 · AWS D1.1 · SSPC
                    </div>
                    <Link
                        to="/settings/profile"
                        data-testid="nav-settings"
                        className="mb-1 flex items-center gap-3 px-2 py-2 font-heading text-sm uppercase tracking-wide text-white/70 hover:text-white"
                    >
                        <Settings size={16} /> Settings
                    </Link>
                    <button
                        data-testid="nav-logout"
                        onClick={async () => {
                            await logout();
                            nav("/login");
                        }}
                        className="flex items-center gap-3 px-2 py-2 font-heading text-sm uppercase tracking-wide text-white/70 hover:text-gold"
                    >
                        <LogOut size={16} /> Sign out
                    </button>
                </div>
            </aside>

            {/* MAIN */}
            <div className={`flex min-w-0 flex-1 flex-col ${impersonation ? "mt-8" : ""}`}>
                <header className="flex h-16 items-center justify-between border-b border-ink-line bg-white px-8">
                    <div className="relative w-full max-w-md">
                        <Search
                            size={16}
                            className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted"
                        />
                        <Input
                            data-testid="topbar-search"
                            placeholder="SEARCH PROJECTS, DRAWINGS, RFIs..."
                            className="h-10 rounded-none border-ink-line bg-background pl-9 font-mono text-xs uppercase tracking-wider text-ink placeholder:text-ink-muted focus-visible:border-gold focus-visible:ring-0"
                        />
                    </div>

                    <div className="flex items-center gap-4">
                        <Popover>
                            <PopoverTrigger asChild>
                                <button
                                    data-testid="topbar-notifications"
                                    className="relative inline-flex items-center justify-center border border-ink-line bg-white p-2 hover:border-navy"
                                >
                                    <Bell size={16} className="text-navy" />
                                    {notifications.unread > 0 && (
                                        <span className="absolute -right-1 -top-1 inline-flex h-4 min-w-[16px] items-center justify-center bg-gold px-1 font-mono text-[10px] font-bold text-navy">
                                            {notifications.unread}
                                        </span>
                                    )}
                                </button>
                            </PopoverTrigger>
                            <PopoverContent
                                align="end"
                                className="w-96 rounded-none border-ink-line p-0"
                            >
                                <div className="flex items-center justify-between border-b border-ink-line bg-navy px-4 py-3 text-white">
                                    <div className="font-heading text-sm uppercase tracking-wider">
                                        Notifications
                                    </div>
                                    <button
                                        onClick={markAllRead}
                                        data-testid="mark-all-read"
                                        className="font-mono text-[10px] uppercase tracking-wider text-gold hover:underline"
                                    >
                                        Mark all read
                                    </button>
                                </div>
                                <div className="max-h-[360px] overflow-y-auto">
                                    {notifications.items.length === 0 && (
                                        <div className="p-6 text-center text-sm text-ink-muted">
                                            No notifications yet
                                        </div>
                                    )}
                                    {notifications.items.map((n) => (
                                        <div
                                            key={n.id}
                                            data-testid={`notification-${n.id}`}
                                            className={`border-b border-ink-line px-4 py-3 ${
                                                !n.is_read ? "bg-gold-pale/40" : ""
                                            }`}
                                        >
                                            <div className="font-heading text-sm font-semibold uppercase tracking-wide text-navy">
                                                {n.title}
                                            </div>
                                            <div className="text-xs text-ink-muted">
                                                {n.message}
                                            </div>
                                            <div className="mt-1 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                                {new Date(n.created_at).toLocaleString()}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </PopoverContent>
                        </Popover>

                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <button
                                    data-testid="topbar-user-menu"
                                    className="flex items-center gap-3 border border-ink-line px-3 py-1.5 hover:border-navy"
                                >
                                    <Avatar className="h-8 w-8 rounded-none border border-navy">
                                        <AvatarFallback className="rounded-none bg-navy font-heading text-xs font-bold text-gold">
                                            {initials}
                                        </AvatarFallback>
                                    </Avatar>
                                    <div className="text-left">
                                        <div className="font-heading text-sm font-semibold uppercase tracking-wide text-navy">
                                            {user?.first_name}
                                        </div>
                                        <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                            {roleLabel(user?.role)}
                                        </div>
                                    </div>
                                </button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent
                                align="end"
                                className="w-56 rounded-none border-ink-line"
                            >
                                <DropdownMenuLabel className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                    {user?.email}
                                </DropdownMenuLabel>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                    onClick={() => nav("/settings/profile")}
                                    className="rounded-none"
                                >
                                    Profile
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                    onClick={() => nav("/settings/security")}
                                    className="rounded-none"
                                >
                                    Security
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                    onClick={async () => {
                                        await logout();
                                        nav("/login");
                                    }}
                                    className="rounded-none text-destructive"
                                >
                                    Sign out
                                </DropdownMenuItem>
                            </DropdownMenuContent>
                        </DropdownMenu>
                    </div>
                </header>

                <main className="flex-1 overflow-y-auto">{children}</main>
            </div>
        </div>
    );
}
