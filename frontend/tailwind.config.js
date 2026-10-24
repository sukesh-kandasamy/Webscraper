/** Tailwind build config. Rebuild the CSS after UI changes:  python scripts/build_css.py */
module.exports = {
  content: ["./public/index.html", "./public/app.js"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "Segoe UI Variable Text", "Segoe UI", "ui-sans-serif", "system-ui", "-apple-system", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["Cascadia Code", "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
      colors: {
        brand: { 50: "#eef2ff", 100: "#e0e7ff", 200: "#c7d2fe", 300: "#a5b4fc", 400: "#818cf8", 500: "#6366f1", 600: "#4f46e5", 700: "#4338ca", 800: "#3730a3", 900: "#312e81" },
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(15 23 42 / 0.04), 0 1px 3px 0 rgb(15 23 42 / 0.06)",
        pop: "0 10px 30px -10px rgb(15 23 42 / 0.25), 0 4px 10px -4px rgb(15 23 42 / 0.12)",
      },
      keyframes: {
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "fade-up": { "0%": { opacity: 0, transform: "translateY(6px)" }, "100%": { opacity: 1, transform: "none" } },
      },
      animation: { shimmer: "shimmer 1.6s infinite", "fade-up": "fade-up .25s ease-out both" },
    },
  },
  plugins: [],
};
