/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ["class"],
    content: ["./src/**/*.{js,jsx,ts,tsx}", "./public/index.html"],
    theme: {
        extend: {
            fontFamily: {
                heading: ["'Sora'", "sans-serif"],
                body: ["'IBM Plex Sans'", "sans-serif"],
                mono: ["'JetBrains Mono'", "monospace"],
                sans: ["'IBM Plex Sans'", "sans-serif"],
            },
            borderRadius: {
                lg: "var(--radius)",
                md: "calc(var(--radius) - 2px)",
                sm: "calc(var(--radius) - 4px)",
            },
            colors: {
                background: "hsl(var(--background))",
                foreground: "hsl(var(--foreground))",
                card: {
                    DEFAULT: "hsl(var(--card))",
                    foreground: "hsl(var(--card-foreground))",
                },
                popover: {
                    DEFAULT: "hsl(var(--popover))",
                    foreground: "hsl(var(--popover-foreground))",
                },
                primary: {
                    DEFAULT: "hsl(var(--primary))",
                    foreground: "hsl(var(--primary-foreground))",
                    mid: "#1a3a5c",
                },
                secondary: {
                    DEFAULT: "hsl(var(--secondary))",
                    foreground: "hsl(var(--secondary-foreground))",
                },
                muted: {
                    DEFAULT: "hsl(var(--muted))",
                    foreground: "hsl(var(--muted-foreground))",
                },
                accent: {
                    DEFAULT: "hsl(var(--accent))",
                    foreground: "hsl(var(--accent-foreground))",
                    light: "#ffd166",
                    pale: "#fff8e6",
                },
                destructive: {
                    DEFAULT: "hsl(var(--destructive))",
                    foreground: "hsl(var(--destructive-foreground))",
                },
                border: "hsl(var(--border))",
                input: "hsl(var(--input))",
                ring: "hsl(var(--ring))",
                navy: {
                    DEFAULT: "#0d2240",
                    mid: "#1a3a5c",
                    deep: "#081629",
                },
                gold: {
                    DEFAULT: "#f5a800",
                    light: "#ffd166",
                    pale: "#fff8e6",
                },
                ink: {
                    DEFAULT: "#1a2d44",
                    muted: "#6b8299",
                    line: "#e2eaf2",
                },
                chart: {
                    1: "hsl(var(--chart-1))",
                    2: "hsl(var(--chart-2))",
                    3: "hsl(var(--chart-3))",
                    4: "hsl(var(--chart-4))",
                    5: "hsl(var(--chart-5))",
                },
            },
            keyframes: {
                "accordion-down": {
                    from: { height: "0" },
                    to: { height: "var(--radix-accordion-content-height)" },
                },
                "accordion-up": {
                    from: { height: "var(--radix-accordion-content-height)" },
                    to: { height: "0" },
                },
                shake: {
                    "0%,100%": { transform: "translateX(0)" },
                    "25%": { transform: "translateX(-6px)" },
                    "75%": { transform: "translateX(6px)" },
                },
                "marquee-x": {
                    from: { transform: "translateX(0%)" },
                    to: { transform: "translateX(-50%)" },
                },
                "pulse-ring": {
                    "0%": { boxShadow: "0 0 0 0 rgba(245,168,0,0.6)" },
                    "70%": { boxShadow: "0 0 0 12px rgba(245,168,0,0)" },
                    "100%": { boxShadow: "0 0 0 0 rgba(245,168,0,0)" },
                },
            },
            animation: {
                "accordion-down": "accordion-down 0.2s ease-out",
                "accordion-up": "accordion-up 0.2s ease-out",
                shake: "shake 0.35s ease-in-out",
                "marquee-x": "marquee-x 30s linear infinite",
                "pulse-ring": "pulse-ring 1.8s infinite",
            },
            backgroundImage: {
                "grid-navy":
                    "linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px)",
                "grid-light":
                    "linear-gradient(rgba(13,34,64,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(13,34,64,0.04) 1px, transparent 1px)",
            },
        },
    },
    plugins: [require("tailwindcss-animate")],
};
