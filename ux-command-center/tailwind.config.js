/** @type {import('tailwindcss').Config} */
export default {
    darkMode: "class",
    content: [
        "./index.html",
        "./index.tsx",
        "./App.tsx",
        "./src/**/*.{js,ts,jsx,tsx}",
        "./components/**/*.{js,ts,jsx,tsx}",
        "./pages/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                primary: "#3b82f6",
                "primary-hover": "#2563eb",
                "background-light": "#F9FAFB",
                "card-light": "#FFFFFF",
                "background-dark": "#0B0E14",
                "card-dark": "#161B22",
                "sidebar-dark": "#0f1116",
                "border-dark": "#21262d",
                "surface-dark": "#1c2128",
                success: "#22c55e",
                danger: "#ef4444",
                warning: "#eab308",
                investment: "#6366f1",
            },
            // CJK fallbacks must stay in sync with --font-sans / --font-mono in
            // src/styles/colors_and_type.css (ADR-028). Inter and Roboto Mono ship no CJK
            // glyphs; without these, Chinese text drops to an unstyled system face.
            fontFamily: {
                sans: ["Inter", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", "sans-serif"],
                display: ["Inter", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", "sans-serif"],
                mono: ["Roboto Mono", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", "monospace"],
            },
        },
    },
    plugins: [],
};
