/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "PingFang SC",
          "Microsoft YaHei",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "SF Mono", "Consolas", "monospace"],
      },
      colors: {
        // Cursor-ish palette: deep slate backgrounds with a vivid accent.
        bg: {
          DEFAULT: "#0b0d12",
          panel: "#12151c",
          elevated: "#181c25",
          hover: "#1f2330",
        },
        line: "#262b36",
        text: {
          DEFAULT: "#e6e9ef",
          muted: "#8b94a7",
          faint: "#5b6478",
        },
        accent: {
          DEFAULT: "#7c8cff",
          hover: "#94a0ff",
        },
        ok: "#3fb950",
        warn: "#d29922",
        err: "#f85149",
      },
      animation: {
        "pulse-soft": "pulseSoft 2s ease-in-out infinite",
      },
      keyframes: {
        pulseSoft: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.4" },
        },
      },
    },
  },
  plugins: [],
};
