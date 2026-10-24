/** Tailwind build config. Rebuild the CSS after UI changes:  python scripts/build_css.py
 *
 * Material Design 3: colour roles and tonal palettes come from src/theme.css (generated from the brand
 * colour by scripts/build_ui_assets.mjs). Role colours (bg-primary, text-on-surface, bg-surface-container…)
 * switch with the theme; the slate / brand / emerald / amber / rose scales point at fixed M3 tones. */
const role = name => `rgb(var(--md-${name}) / <alpha-value>)`;
const ROLES = ["primary", "on-primary", "primary-container", "on-primary-container", "secondary", "on-secondary",
  "secondary-container", "on-secondary-container", "tertiary", "on-tertiary", "tertiary-container",
  "on-tertiary-container", "error", "on-error", "error-container", "on-error-container", "surface", "on-surface",
  "on-surface-variant", "surface-dim", "surface-bright", "surface-container-lowest", "surface-container-low",
  "surface-container", "surface-container-high", "surface-container-highest", "outline", "outline-variant",
  "inverse-surface", "inverse-on-surface", "inverse-primary", "scrim", "shadow", "success", "on-success",
  "success-container", "on-success-container", "warning", "on-warning", "warning-container", "on-warning-container"];
// Tailwind shade -> Material tone
const SHADES = { 50: 98, 100: 95, 200: 90, 300: 80, 400: 70, 500: 50, 600: 40, 700: 30, 800: 20, 900: 12, 950: 6 };
const palette = key => Object.fromEntries(Object.entries(SHADES).map(([s, t]) => [s, `rgb(var(--tone-${key}-${t}) / <alpha-value>)`]));

module.exports = {
  content: ["./templates/**/*.html", "./public/js/**/*.js"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Roboto", "Segoe UI", "system-ui", "-apple-system", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["Roboto Mono", "Cascadia Code", "ui-monospace", "Consolas", "monospace"],
      },
      colors: {
        ...Object.fromEntries(ROLES.map(r => [r, role(r)])),
        slate: palette("nv"), gray: palette("n"), brand: palette("p"), indigo: palette("p"),
        emerald: palette("s"), green: palette("s"), amber: palette("w"), rose: palette("e"), red: palette("e"),
      },
      opacity: { 8: "0.08", 12: "0.12", 16: "0.16", 38: "0.38" },
      borderRadius: { "m3-xs": "4px", "m3-sm": "8px", "m3-md": "12px", "m3-lg": "16px", "m3-xl": "28px" },  // M3 shape scale
      boxShadow: {                                                                       // M3 elevation 1-3
        card: "0 1px 2px 0 rgb(0 0 0 / 0.30), 0 1px 3px 1px rgb(0 0 0 / 0.15)",
        e1: "0 1px 2px 0 rgb(0 0 0 / 0.30), 0 1px 3px 1px rgb(0 0 0 / 0.15)",
        e2: "0 1px 2px 0 rgb(0 0 0 / 0.30), 0 2px 6px 2px rgb(0 0 0 / 0.15)",
        e3: "0 1px 3px 0 rgb(0 0 0 / 0.30), 0 4px 8px 3px rgb(0 0 0 / 0.15)",
        pop: "0 1px 3px 0 rgb(0 0 0 / 0.30), 0 4px 8px 3px rgb(0 0 0 / 0.15)",
      },
      transitionTimingFunction: { emphasized: "cubic-bezier(0.2, 0, 0, 1)", standard: "cubic-bezier(0.2, 0, 0, 1)" },
      keyframes: {
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "fade-up": { "0%": { opacity: 0, transform: "translateY(8px)" }, "100%": { opacity: 1, transform: "none" } },
        indeterminate: { "0%": { left: "-40%", width: "40%" }, "100%": { left: "100%", width: "40%" } },
      },
      animation: { shimmer: "shimmer 1.6s infinite", "fade-up": "fade-up .3s cubic-bezier(0.2, 0, 0, 1) both",
                   indeterminate: "indeterminate 1.4s cubic-bezier(0.2, 0, 0, 1) infinite" },
    },
  },
  plugins: [],
};
