import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import {
    ArrowRight,
    BadgeCheck,
    Binary,
    Boxes,
    Brain,
    Building2,
    ChartLine,
    CircleCheck,
    CircleCheckBig,
    CircleDot,
    ClipboardCheck,
    Cpu,
    Download,
    Factory,
    FileStack,
    Flame,
    GitMerge,
    Hexagon,
    Layers,
    Leaf,
    Link2,
    MessagesSquare,
    PackageCheck,
    Play,
    Rocket,
    Ruler,
    Shield,
    ShieldCheck,
    Sparkles,
    Upload,
    Zap,
} from "lucide-react";
import { Logo } from "@/components/brand/Logo";
import { Button } from "@/components/ui/button";

const STATS = [
    { k: "25", l: "Analysis Modes" },
    { k: "500 MB", l: "Drawing Packages" },
    { k: "< 2 min", l: "To Full Report" },
    { k: "95%+", l: "QC Accuracy" },
];

const FEATURES = [
    {
        icon: ClipboardCheck,
        title: "Drawing Checker QC",
        body: "AISC 360-22 · AWS D1.1 · SSPC enforcement with findings graded Critical · Major · Minor. Every clause cited.",
    },
    {
        icon: PackageCheck,
        title: "Material Take-Off",
        body: "200+ profile library, tonnage roll-ups, bolt & plate schedules, ready-to-paste procurement tables.",
    },
    {
        icon: ChartLine,
        title: "Estimation Pro",
        body: "Sensitivity analysis, hidden-rate exposure, 3 bid scenarios, per-ton and per-piece commercial clarity.",
    },
    {
        icon: Brain,
        title: "Full Project Audit",
        body: "12-section master intake — scope, anchors, RFIs, schedule, code matrix, risk register, VE ideas.",
    },
    {
        icon: ShieldCheck,
        title: "SHA-256 Audit Trail",
        body: "Every analysis fingerprinted and verifiable. Polygon anchoring on the roadmap — tamper-evident today.",
    },
    {
        icon: Ruler,
        title: "Role-Aware Output",
        body: "Detailer sees shop notes, Estimator sees cost levers. One prompt, six personas, zero cognitive load.",
    },
];

const MODES = [
    { icon: FileStack, name: "Full Project Audit", group: "Intake" },
    { icon: ClipboardCheck, name: "Drawing Checker QC", group: "QC" },
    { icon: Boxes, name: "Material Take-Off", group: "Quant" },
    { icon: ChartLine, name: "Estimation Pro", group: "Commercial" },
    { icon: GitMerge, name: "Clash Detector", group: "QC" },
    { icon: Cpu, name: "CNC File Checker", group: "QC" },
    { icon: Link2, name: "Connection Design", group: "Engineering" },
    { icon: Leaf, name: "Sustainability", group: "Specialist" },
    { icon: Flame, name: "Bid Strategy", group: "Commercial" },
    { icon: Shield, name: "Safety Plan", group: "Specialist" },
    { icon: MessagesSquare, name: "Chat Assistant", group: "Assistant" },
    { icon: Layers, name: "Submittal Tracker", group: "Schedule" },
];

const PERSONAS = [
    { role: "Detailer", wins: ["Auto QC of shop drawings", "Connection clarity", "Revision diff"] },
    { role: "Estimator", wins: ["Take-off in minutes", "Hidden cost exposure", "Bid scenarios"] },
    { role: "Engineer", wins: ["AISC + AWS code checks", "Connection advisor", "Clash detection"] },
    { role: "Fabricator", wins: ["NC1 / DSTV validation", "Cut list sanity", "Procurement pack"] },
    { role: "PM", wins: ["Submittal tracker", "Risk register", "Change orders"] },
    { role: "Modular", wins: ["Kit-of-parts scope", "Interface control", "Erection plan"] },
];

const COMPARE = [
    ["Drawing QC vs AISC 360-22", true, false],
    ["Material Take-Off (200+ profiles)", true, "Limited"],
    ["Role-aware output", true, false],
    ["SHA-256 audit trail", true, false],
    ["Full 9-stage lifecycle", true, "Fragments"],
    ["Sustainability + Carbon Report", true, false],
    ["Multi-format export (Word/PDF/XLSX/CSV/MD)", true, "Partial"],
];

const PRICES = [
    {
        plan: "Free",
        price: "$0",
        cadence: "forever",
        body: ["5 analyses / month", "10 MB files", "Phase-1 · Summary · Chat", "Markdown export"],
        cta: "Start free",
        dim: true,
    },
    {
        plan: "Starter",
        price: "$49",
        cadence: "per month",
        body: ["30 analyses / month", "50 MB files", "15 core modes", "PDF · Word · CSV export"],
        cta: "Go Starter",
    },
    {
        plan: "Pro",
        price: "$149",
        cadence: "per month",
        featured: true,
        body: [
            "150 analyses / month",
            "200 MB files",
            "All 25 modes",
            "All export formats",
            "Priority queue · API access",
        ],
        cta: "Go Pro",
    },
    {
        plan: "Enterprise",
        price: "Custom",
        cadence: "talk to us",
        body: [
            "Unlimited analyses",
            "500 MB files",
            "SSO · SLA · white-label",
            "Dedicated success engineer",
        ],
        cta: "Contact sales",
    },
];

const TESTIMONIALS = [
    {
        quote: "StructMind turned a 3-day drawing review into a 4-minute report. The AISC cross-reference alone pays for itself.",
        name: "Daniel O'Brien",
        role: "Detailing Manager · Brooklyn Steel",
        country: "USA",
    },
    {
        quote: "Finally a platform that speaks fabricator. The NC1 checker caught three hole-pattern errors that would have been scrapped on the shop floor.",
        name: "Aisling Murphy",
        role: "Fab Ops Lead · Auckland Structures",
        country: "NZ",
    },
    {
        quote: "Estimation Pro's sensitivity table is uncanny. We bid tighter and win more with fewer sleepless nights.",
        name: "Ravi Subramanian",
        role: "Commercial Director · Chennai Metalworks",
        country: "India",
    },
];

export default function Landing() {
    return (
        <div className="min-h-screen bg-background text-ink">
            {/* NAV */}
            <header
                data-testid="landing-navbar"
                className="sticky top-0 z-40 border-b border-ink-line bg-white/80 backdrop-blur-xl"
            >
                <div className="container-steel flex h-16 items-center justify-between">
                    <Logo variant="dark" size="md" />
                    <nav className="hidden items-center gap-8 font-heading text-sm uppercase tracking-[0.16em] text-ink-muted md:flex">
                        <a href="#features" className="hover:text-navy">Features</a>
                        <a href="#modes" className="hover:text-navy">Modes</a>
                        <a href="#pricing" className="hover:text-navy">Pricing</a>
                        <a href="#about" className="hover:text-navy">About</a>
                    </nav>
                    <div className="flex items-center gap-3">
                        <Link
                            to="/login"
                            data-testid="nav-login-btn"
                            className="btn-ghost-navy"
                        >
                            Sign In
                        </Link>
                        <Link
                            to="/signup"
                            data-testid="nav-signup-btn"
                            className="btn-gold"
                        >
                            Get Started
                        </Link>
                    </div>
                </div>
            </header>

            {/* HERO */}
            <section className="relative overflow-hidden bg-navy text-white noise">
                <div className="absolute inset-0 tech-grid opacity-60" />
                <div
                    className="absolute inset-0"
                    style={{
                        backgroundImage:
                            "url('https://images.unsplash.com/photo-1748956628042-b73331e0b479?crop=entropy&cs=srgb&fm=jpg&q=85')",
                        backgroundSize: "cover",
                        backgroundPosition: "center",
                        opacity: 0.2,
                    }}
                />
                <div className="absolute inset-0 bg-gradient-to-r from-navy via-navy/95 to-navy/70" />
                <div className="container-steel relative z-10 grid gap-12 py-24 md:grid-cols-12 md:py-36">
                    <motion.div
                        initial={{ opacity: 0, y: 24 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.8, ease: "easeOut" }}
                        className="md:col-span-7"
                    >
                        <div className="mb-6 inline-flex items-center gap-2 border border-gold/40 bg-gold/10 px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.24em] text-gold">
                            <CircleDot size={12} />
                            AISC 16th Ed · AWS D1.1 · SSPC · OSHA
                        </div>
                        <h1 className="font-heading text-[56px] font-black leading-[0.95] tracking-tight text-white md:text-[92px]">
                            Struct<span className="text-gold">Mind</span>
                            <span className="block font-mono text-sm font-medium uppercase tracking-[0.3em] text-white/70 md:text-base">
                                Structural Intelligence · 4X Core Engine
                            </span>
                        </h1>
                        <p className="mt-8 max-w-xl font-heading text-xl font-light leading-snug text-white/80 md:text-2xl">
                            The most advanced platform for structural steel
                            detailing, estimation, and fabrication.
                        </p>
                        <p className="mt-6 max-w-xl text-base leading-relaxed text-white/70">
                            Drop a 500 MB drawing package. Pick your persona. Get a
                            court-admissible, code-enforced report in under 2 minutes —
                            with Word, PDF, Excel, CSV exports and SHA-256 audit trail.
                        </p>
                        <div className="mt-10 flex flex-wrap items-center gap-4">
                            <Link
                                to="/signup"
                                data-testid="hero-cta-primary"
                                className="btn-gold group"
                            >
                                Start Free Trial
                                <ArrowRight
                                    size={16}
                                    className="transition-transform group-hover:translate-x-1"
                                />
                            </Link>
                            <a
                                href="#modes"
                                data-testid="hero-cta-secondary"
                                className="btn-ghost-light"
                            >
                                <Play size={14} fill="currentColor" />
                                Explore 25 Modes
                            </a>
                        </div>

                        <div className="mt-14 grid max-w-xl grid-cols-4 gap-0 border-y border-white/10">
                            {STATS.map((s) => (
                                <div
                                    key={s.l}
                                    className="border-r border-white/10 py-4 pl-4 pr-2 last:border-r-0"
                                >
                                    <div className="font-heading text-2xl font-black uppercase text-gold md:text-3xl">
                                        {s.k}
                                    </div>
                                    <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-white/55">
                                        {s.l}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </motion.div>

                    {/* Floating UI card */}
                    <motion.div
                        initial={{ opacity: 0, y: 40 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.9, delay: 0.2 }}
                        className="relative hidden md:col-span-5 md:block"
                    >
                        <div className="absolute inset-4 border border-gold/40" />
                        <div className="relative ml-6 mt-6 bg-white p-6 text-ink shadow-[0_40px_80px_-40px_rgba(0,0,0,0.5)]">
                            <div className="flex items-center justify-between border-b border-ink-line pb-3">
                                <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                    STRUCTMIND · LIVE ANALYSIS
                                </div>
                                <div className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-success">
                                    <span className="h-2 w-2 animate-pulse-ring rounded-full bg-success" />
                                    Processing
                                </div>
                            </div>
                            <div className="mt-4 font-heading text-xl font-bold uppercase tracking-tight text-navy">
                                Drawing Checker · QC Run
                            </div>
                            <div className="mt-1 font-mono text-xs text-ink-muted">
                                NORTHGATE RESORT · REV C · 84 SHEETS
                            </div>
                            <div className="mt-6 space-y-2">
                                {[
                                    { l: "Uploading", pct: 100, done: true },
                                    { l: "Parsing", pct: 100, done: true },
                                    { l: "Analyzing", pct: 72, done: false },
                                    { l: "Exporting", pct: 0, done: false },
                                ].map((r) => (
                                    <div key={r.l}>
                                        <div className="flex justify-between font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                            <span>{r.l}</span>
                                            <span>{r.pct}%</span>
                                        </div>
                                        <div className="mt-1 h-1.5 w-full bg-ink-line">
                                            <div
                                                className={`h-full ${r.done ? "bg-success" : "bg-gold"}`}
                                                style={{ width: `${r.pct}%` }}
                                            />
                                        </div>
                                    </div>
                                ))}
                            </div>
                            <div className="mt-6 grid grid-cols-3 gap-3 border-t border-ink-line pt-4">
                                <div>
                                    <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">Critical</div>
                                    <div className="font-heading text-2xl font-black text-destructive">04</div>
                                </div>
                                <div>
                                    <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">Major</div>
                                    <div className="font-heading text-2xl font-black text-warning">12</div>
                                </div>
                                <div>
                                    <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">Minor</div>
                                    <div className="font-heading text-2xl font-black text-success">31</div>
                                </div>
                            </div>
                        </div>
                    </motion.div>
                </div>

                {/* Trust strip */}
                <div className="relative z-10 border-t border-white/10 bg-navy-mid/40 py-6">
                    <div className="container-steel flex flex-wrap items-center justify-center gap-6 font-mono text-[11px] uppercase tracking-[0.3em] text-white/60">
                        <span>Trusted by detailers across</span>
                        <span className="text-gold">USA</span>
                        <span>·</span>
                        <span className="text-gold">Canada</span>
                        <span>·</span>
                        <span className="text-gold">Australia</span>
                        <span>·</span>
                        <span className="text-gold">India</span>
                        <span>·</span>
                        <span className="text-gold">APAC</span>
                    </div>
                </div>
            </section>

            {/* HOW IT WORKS */}
            <section className="relative border-y border-ink-line bg-navy py-24 text-white noise">
                <div className="absolute inset-0 tech-grid opacity-50" />
                <div className="container-steel relative">
                    <div className="mb-16 flex flex-col items-start gap-3">
                        <span className="text-overline">Workflow</span>
                        <h2 className="font-heading text-4xl font-black uppercase tracking-tight text-white md:text-6xl">
                            Three moves. One report.
                        </h2>
                    </div>
                    <div className="grid gap-0 border border-white/10 md:grid-cols-3">
                        {[
                            {
                                no: "01",
                                icon: Upload,
                                title: "Upload",
                                body: "Drop PDF · DWG · DXF · IFC · RVT · NWD up to 500 MB. We chunk the monsters.",
                            },
                            {
                                no: "02",
                                icon: Sparkles,
                                title: "Select Mode",
                                body: "Pick from 25 modes — from Full Project Audit to Connection Design Advisor.",
                            },
                            {
                                no: "03",
                                icon: Download,
                                title: "Export",
                                body: "Word, PDF, Excel, CSV, Markdown — all generated automatically. SHA-256 anchored.",
                            },
                        ].map((s, i) => {
                            const Icon = s.icon;
                            return (
                                <div
                                    key={s.no}
                                    className="relative border-b border-white/10 p-10 md:border-b-0 md:border-r md:last:border-r-0"
                                >
                                    <div className="font-mono text-xs uppercase tracking-[0.3em] text-gold">
                                        STEP {s.no}
                                    </div>
                                    <Icon size={42} className="mt-6 text-gold" />
                                    <div className="mt-6 font-heading text-3xl font-black uppercase tracking-tight text-white">
                                        {s.title}
                                    </div>
                                    <div className="mt-4 text-base leading-relaxed text-white/70">
                                        {s.body}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </section>

            {/* FEATURES */}
            <section id="features" className="bg-background py-24">
                <div className="container-steel">
                    <div className="mb-16 grid gap-6 md:grid-cols-12">
                        <div className="md:col-span-5">
                            <span className="text-overline">What you get</span>
                            <h2 className="mt-3 font-heading text-4xl font-black uppercase leading-[0.95] tracking-tight text-navy md:text-6xl">
                                Every<br />detailing<br />superpower,<br />
                                <span className="text-gold">in one console.</span>
                            </h2>
                        </div>
                        <div className="md:col-span-7">
                            <p className="text-lg leading-relaxed text-ink-muted">
                                STRUCTMIND was built by fabricators and detailers who were
                                tired of toggling between drawings, spreadsheets, and code
                                books. Every feature below is production-grade — today.
                            </p>
                        </div>
                    </div>
                    <div className="grid gap-0 border border-ink-line md:grid-cols-3">
                        {FEATURES.map((f, i) => {
                            const Icon = f.icon;
                            return (
                                <motion.div
                                    key={f.title}
                                    initial={{ opacity: 0, y: 20 }}
                                    whileInView={{ opacity: 1, y: 0 }}
                                    viewport={{ once: true }}
                                    transition={{ duration: 0.6, delay: i * 0.08 }}
                                    className="group border-b border-r border-ink-line bg-white p-8 transition-colors hover:bg-navy hover:text-white md:odd:border-l-0"
                                >
                                    <Icon
                                        size={28}
                                        className="text-navy group-hover:text-gold"
                                    />
                                    <div className="mt-6 font-heading text-2xl font-bold uppercase tracking-tight">
                                        {f.title}
                                    </div>
                                    <p className="mt-4 text-sm leading-relaxed text-ink-muted group-hover:text-white/80">
                                        {f.body}
                                    </p>
                                </motion.div>
                            );
                        })}
                    </div>
                </div>
            </section>

            {/* MODES SHOWCASE */}
            <section id="modes" className="border-y border-ink-line bg-white py-24">
                <div className="container-steel">
                    <div className="mb-12 flex flex-col items-start justify-between gap-6 md:flex-row md:items-end">
                        <div>
                            <span className="text-overline">25 Modes</span>
                            <h2 className="mt-3 font-heading text-4xl font-black uppercase tracking-tight text-navy md:text-5xl">
                                The most comprehensive<br />
                                <span className="text-gold">detailing library.</span>
                            </h2>
                        </div>
                        <p className="max-w-md text-ink-muted">
                            16 battle-tested modes + 9 exclusive new analysers —
                            Clash Detector, Sustainability, Value Engineering, Change Orders,
                            Safety Plans, and more.
                        </p>
                    </div>
                    <div className="grid gap-0 border border-ink-line sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
                        {MODES.map((m, i) => {
                            const Icon = m.icon;
                            return (
                                <div
                                    key={m.name}
                                    data-testid={`mode-card-${i}`}
                                    className="group border-b border-r border-ink-line p-6 transition-colors hover:bg-gold-pale"
                                >
                                    <div className="flex items-start justify-between">
                                        <Icon size={22} className="text-navy" />
                                        <span className="font-mono text-[9px] uppercase tracking-widest text-ink-muted">
                                            {m.group}
                                        </span>
                                    </div>
                                    <div className="mt-5 font-heading text-lg font-bold uppercase tracking-tight text-navy">
                                        {m.name}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                    <div className="mt-6 text-center font-mono text-xs uppercase tracking-[0.2em] text-ink-muted">
                        + 13 more modes available in-app · <span className="text-gold">sign up to see all 25</span>
                    </div>
                </div>
            </section>

            {/* PERSONAS */}
            <section className="bg-background py-24">
                <div className="container-steel">
                    <span className="text-overline">Role-aware intelligence</span>
                    <h2 className="mt-3 font-heading text-4xl font-black uppercase tracking-tight text-navy md:text-5xl">
                        One prompt. <span className="text-gold">Six personas.</span>
                    </h2>
                    <div className="mt-12 grid gap-0 border border-ink-line md:grid-cols-3 lg:grid-cols-6">
                        {PERSONAS.map((p) => (
                            <div
                                key={p.role}
                                className="border-b border-r border-ink-line bg-white p-6 last:border-r-0"
                            >
                                <div className="font-heading text-xl font-bold uppercase tracking-tight text-navy">
                                    {p.role}
                                </div>
                                <ul className="mt-4 space-y-2 text-sm text-ink-muted">
                                    {p.wins.map((w) => (
                                        <li key={w} className="flex items-start gap-2">
                                            <CircleCheck size={14} className="mt-0.5 text-gold" />
                                            {w}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ))}
                    </div>
                </div>
            </section>

            {/* COMPARISON */}
            <section className="border-y border-ink-line bg-white py-24">
                <div className="container-steel">
                    <div className="mb-12 flex flex-col items-start justify-between gap-4 md:flex-row md:items-end">
                        <div>
                            <span className="text-overline">Head-to-head</span>
                            <h2 className="mt-3 font-heading text-4xl font-black uppercase tracking-tight text-navy md:text-5xl">
                                StructMind vs.<br />
                                <span className="text-ink-muted">Other tools</span>
                            </h2>
                        </div>
                    </div>
                    <div className="overflow-hidden border border-ink-line">
                        <table className="w-full text-left">
                            <thead className="bg-navy text-white">
                                <tr>
                                    <th className="px-6 py-4 font-heading text-sm uppercase tracking-wider">
                                        Capability
                                    </th>
                                    <th className="px-6 py-4 font-heading text-sm uppercase tracking-wider text-gold">
                                        STRUCTMIND
                                    </th>
                                    <th className="px-6 py-4 font-heading text-sm uppercase tracking-wider text-white/60">
                                        Legacy tools
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                {COMPARE.map((r) => (
                                    <tr
                                        key={r[0]}
                                        className="border-t border-ink-line bg-white"
                                    >
                                        <td className="px-6 py-3 text-sm text-ink">{r[0]}</td>
                                        <td className="px-6 py-3">
                                            <BadgeCheck className="text-gold" size={18} />
                                        </td>
                                        <td className="px-6 py-3 font-mono text-xs uppercase tracking-wider text-ink-muted">
                                            {r[2] === false ? "Not available" : r[2]}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            </section>

            {/* PRICING */}
            <section id="pricing" className="bg-background py-24">
                <div className="container-steel">
                    <span className="text-overline">Pricing</span>
                    <h2 className="mt-3 font-heading text-4xl font-black uppercase tracking-tight text-navy md:text-5xl">
                        Start free. Scale on your terms.
                    </h2>
                    <div className="mt-12 grid gap-0 border border-ink-line md:grid-cols-2 lg:grid-cols-4">
                        {PRICES.map((p) => (
                            <div
                                key={p.plan}
                                data-testid={`price-${p.plan.toLowerCase()}`}
                                className={`relative border-b border-r border-ink-line p-8 ${
                                    p.featured ? "bg-navy text-white" : "bg-white"
                                }`}
                            >
                                {p.featured && (
                                    <div className="absolute -top-4 left-8 bg-gold px-3 py-1 font-mono text-[10px] uppercase tracking-wider text-navy">
                                        Most popular
                                    </div>
                                )}
                                <div
                                    className={`font-heading text-2xl font-bold uppercase tracking-tight ${
                                        p.featured ? "text-white" : "text-navy"
                                    }`}
                                >
                                    {p.plan}
                                </div>
                                <div className="mt-4 flex items-baseline gap-2">
                                    <span
                                        className={`font-heading text-5xl font-black ${
                                            p.featured ? "text-gold" : "text-navy"
                                        }`}
                                    >
                                        {p.price}
                                    </span>
                                    <span
                                        className={`font-mono text-xs uppercase tracking-wider ${
                                            p.featured ? "text-white/60" : "text-ink-muted"
                                        }`}
                                    >
                                        {p.cadence}
                                    </span>
                                </div>
                                <ul className="my-6 space-y-2 text-sm">
                                    {p.body.map((b) => (
                                        <li
                                            key={b}
                                            className={`flex gap-2 ${
                                                p.featured
                                                    ? "text-white/80"
                                                    : "text-ink-muted"
                                            }`}
                                        >
                                            <CircleCheckBig
                                                size={14}
                                                className={`mt-1 ${
                                                    p.featured
                                                        ? "text-gold"
                                                        : "text-navy"
                                                }`}
                                            />
                                            {b}
                                        </li>
                                    ))}
                                </ul>
                                <Link
                                    to="/signup"
                                    className={
                                        p.featured ? "btn-gold w-full" : "btn-navy w-full"
                                    }
                                >
                                    {p.cta}
                                </Link>
                            </div>
                        ))}
                    </div>
                </div>
            </section>

            {/* TESTIMONIALS */}
            <section className="border-y border-ink-line bg-white py-24">
                <div className="container-steel">
                    <span className="text-overline">Loved by teams</span>
                    <h2 className="mt-3 font-heading text-4xl font-black uppercase tracking-tight text-navy md:text-5xl">
                        From the<br />
                        <span className="text-gold">detailing floor.</span>
                    </h2>
                    <div className="mt-12 grid gap-0 border border-ink-line md:grid-cols-3">
                        {TESTIMONIALS.map((t, i) => (
                            <motion.div
                                key={t.name}
                                initial={{ opacity: 0, y: 16 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.1 }}
                                className="border-b border-r border-ink-line p-8 last:border-r-0"
                            >
                                <div className="font-mono text-gold text-xs tracking-[0.3em]">
                                    ★★★★★
                                </div>
                                <p className="mt-4 font-heading text-lg leading-snug text-ink">
                                    "{t.quote}"
                                </p>
                                <div className="mt-6 border-t border-ink-line pt-4">
                                    <div className="font-heading text-sm font-bold uppercase tracking-wide text-navy">
                                        {t.name}
                                    </div>
                                    <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                        {t.role} · {t.country}
                                    </div>
                                </div>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* FINAL CTA */}
            <section className="relative overflow-hidden bg-navy py-20 text-white noise">
                <div className="absolute inset-0 tech-grid opacity-60" />
                <div className="container-steel relative z-10 flex flex-col items-start justify-between gap-8 md:flex-row md:items-center">
                    <div>
                        <h2 className="font-heading text-4xl font-black uppercase tracking-tight md:text-5xl">
                            Ship the bid by lunch.
                        </h2>
                        <p className="mt-3 max-w-xl text-white/70">
                            Create your free account and run your first analysis in under
                            5 minutes. No credit card.
                        </p>
                    </div>
                    <div className="flex gap-3">
                        <Link to="/signup" className="btn-gold">
                            Create free account
                            <Rocket size={16} />
                        </Link>
                        <Link to="/login" className="btn-ghost-light">
                            Sign in
                        </Link>
                    </div>
                </div>
            </section>

            {/* FOOTER */}
            <footer
                id="about"
                className="border-t border-ink-line bg-white py-12"
            >
                <div className="container-steel grid gap-10 md:grid-cols-12">
                    <div className="md:col-span-5">
                        <Logo variant="dark" size="md" />
                        <p className="mt-4 max-w-md text-sm leading-relaxed text-ink-muted">
                            STRUCTMIND is the structural-steel intelligence platform by
                            4XStruct Inc. Built for fabricators, detailers, engineers,
                            estimators, project managers, and modular specialists.
                        </p>
                    </div>
                    <div className="md:col-span-2">
                        <div className="text-overline">Product</div>
                        <ul className="mt-4 space-y-2 font-mono text-xs uppercase tracking-wider text-ink-muted">
                            <li><a href="#features" className="hover:text-navy">Features</a></li>
                            <li><a href="#modes" className="hover:text-navy">Modes</a></li>
                            <li><a href="#pricing" className="hover:text-navy">Pricing</a></li>
                        </ul>
                    </div>
                    <div className="md:col-span-2">
                        <div className="text-overline">Company</div>
                        <ul className="mt-4 space-y-2 font-mono text-xs uppercase tracking-wider text-ink-muted">
                            <li>4XStruct Inc.</li>
                            <li>Careers</li>
                            <li>Contact</li>
                        </ul>
                    </div>
                    <div className="md:col-span-3">
                        <div className="text-overline">Standards</div>
                        <ul className="mt-4 space-y-2 font-mono text-xs uppercase tracking-wider text-ink-muted">
                            <li>AISC 360-22 · 341 · 358</li>
                            <li>AWS D1.1 · D1.8</li>
                            <li>ASTM · RCSC · SSPC · OSHA 1926</li>
                        </ul>
                    </div>
                </div>
                <div className="container-steel mt-10 flex flex-col items-start justify-between gap-2 border-t border-ink-line pt-6 text-xs text-ink-muted md:flex-row md:items-center">
                    <div>
                        © {new Date().getFullYear()} 4XStruct Inc. · All rights reserved.
                    </div>
                    <div className="font-mono uppercase tracking-wider">
                        STRUCTMIND CORE engine · SHA-256 audit trail
                    </div>
                </div>
            </footer>
        </div>
    );
}
