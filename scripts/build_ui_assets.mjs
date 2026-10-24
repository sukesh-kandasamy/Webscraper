// Build the UI's design assets from Google's packages (run only when changing them):
//
//     node scripts/build_ui_assets.mjs
//
// Writes, and these files are committed so the app never needs npm at run time:
//   frontend/src/theme.css          Material 3 colour roles (light + dark) from our brand seed colour
//   frontend/public/fonts/          Roboto (variable, latin + latin-ext) and its licence
//   frontend/public/js/icons.js     the Material Symbols (Rounded) icons the UI uses, as SVG paths
//
// Packages (installed into a temporary folder, not the project):
//   @material/material-color-utilities (Apache-2.0), @fontsource-variable/roboto (OFL-1.1),
//   @material-symbols/svg-400 (Apache-2.0)
import { execSync } from "node:child_process";
import { register } from "node:module";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const UI = join(ROOT, "frontend");
const SEED = "#4F46E5";            // brand indigo (logo)
const PKGS = ["@material/material-color-utilities@0.4.0", "@fontsource-variable/roboto@5.3.0", "@material-symbols/svg-400@0.48.1"];

const work = join(tmpdir(), "universal-scraper-ui-build");
mkdirSync(work, { recursive: true });
if (!existsSync(join(work, "package.json"))) writeFileSync(join(work, "package.json"), '{"private": true}');
console.log("installing", PKGS.join(", "));
execSync(`npm install --no-audit --no-fund --silent ${PKGS.join(" ")}`, { cwd: work, stdio: "inherit" });
const mod = p => join(work, "node_modules", p);

// ---------------------------------------------------------------- colour roles
// The colour package imports its own files without ".js" (written for bundlers); add it for Node.
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(spec, ctx, next) {
    try { return await next(spec, ctx); }
    catch (e) { if (/^\\.\\.?\\//.test(spec) && !spec.endsWith(".js")) return next(spec + ".js", ctx); throw e; }
  }`));
const mcu = await import(pathToFileURL(mod("@material/material-color-utilities/index.js")).href);
const { argbFromHex, Hct, SchemeFidelity, MaterialDynamicColors, customColor, redFromArgb, greenFromArgb, blueFromArgb } = mcu;
const ROLES = ["primary", "onPrimary", "primaryContainer", "onPrimaryContainer", "secondary", "onSecondary",
  "secondaryContainer", "onSecondaryContainer", "tertiary", "onTertiary", "tertiaryContainer", "onTertiaryContainer",
  "error", "onError", "errorContainer", "onErrorContainer", "surface", "onSurface", "onSurfaceVariant",
  "surfaceDim", "surfaceBright", "surfaceContainerLowest", "surfaceContainerLow", "surfaceContainer",
  "surfaceContainerHigh", "surfaceContainerHighest", "outline", "outlineVariant", "inverseSurface",
  "inverseOnSurface", "inversePrimary", "scrim", "shadow"];
const kebab = s => s.replace(/[A-Z]/g, m => "-" + m.toLowerCase());
const rgb = argb => `${redFromArgb(argb)} ${greenFromArgb(argb)} ${blueFromArgb(argb)}`;
const source = argbFromHex(SEED);
function vars(dark) {
  const scheme = new SchemeFidelity(Hct.fromInt(source), dark, 0);
  const lines = ROLES.map(r => `  --md-${kebab(r)}: ${rgb(MaterialDynamicColors[r].getArgb(scheme))};`);
  // Extra roles Material 3 leaves to apps: success (green) and warning (amber), harmonized with the seed.
  for (const [name, hex] of [["success", "#1E8E3E"], ["warning", "#E37400"]]) {
    const c = customColor(source, { name, value: argbFromHex(hex), blend: name !== "warning" })[dark ? "dark" : "light"];
    lines.push(`  --md-${name}: ${rgb(c.color)};`, `  --md-on-${name}: ${rgb(c.onColor)};`,
               `  --md-${name}-container: ${rgb(c.colorContainer)};`, `  --md-on-${name}-container: ${rgb(c.onColorContainer)};`);
  }
  return lines.join("\n");
}
// Tonal palettes (fixed tones, same in light and dark): Tailwind's slate / brand / emerald / amber / rose
// scales are mapped to them, so every existing utility class follows the Material 3 colours.
const { TonalPalette, Blend } = mcu;
const base = new SchemeFidelity(Hct.fromInt(source), false, 0);
const PALETTES = {
  p: base.primaryPalette, n: base.neutralPalette, nv: base.neutralVariantPalette, e: base.errorPalette,
  s: TonalPalette.fromInt(Blend.harmonize(argbFromHex("#1E8E3E"), source)),
  w: TonalPalette.fromInt(argbFromHex("#E37400")),          // amber stays amber (harmonizing turns it red)
};
const TONES = [99, 98, 96, 95, 92, 90, 87, 80, 70, 60, 50, 40, 30, 22, 20, 17, 12, 10, 6, 4];
const tones = Object.entries(PALETTES).flatMap(([k, pal]) => TONES.map(t => `  --tone-${k}-${t}: ${rgb(pal.tone(t))};`)).join("\n");
writeFileSync(join(UI, "src", "theme.css"), `/* Material 3 colour roles and tonal palettes generated from ${SEED} by scripts/build_ui_assets.mjs.
   Do not edit by hand. Values are "R G B" so Tailwind can add opacity: rgb(var(--md-primary) / 0.12). */
:root {
${vars(false)}
${tones}
}
.dark {
${vars(true)}
}
`);
console.log("wrote frontend/src/theme.css");

// ---------------------------------------------------------------- Roboto
const fonts = join(UI, "public", "fonts");
mkdirSync(fonts, { recursive: true });
for (const f of ["roboto-latin-wght-normal.woff2", "roboto-latin-ext-wght-normal.woff2"]) {
  copyFileSync(mod(`@fontsource-variable/roboto/files/${f}`), join(fonts, f));
}
copyFileSync(mod("@fontsource-variable/roboto/LICENSE"), join(fonts, "ROBOTO-LICENSE.txt"));
console.log("wrote frontend/public/fonts/");

// ---------------------------------------------------------------- Material Symbols
const ICONS = ["home", "add", "folder", "folder_open", "table_chart", "settings", "smart_toy", "wand_stars", "search",
  "download", "play_arrow", "refresh", "edit", "delete", "content_copy", "stop", "open_in_new", "more_vert", "check",
  "check_circle", "warning", "info", "error", "light_mode", "dark_mode", "brightness_auto", "menu", "close",
  "chevron_left", "chevron_right", "arrow_back", "arrow_forward", "bolt", "view_list", "schedule", "lock",
  "shopping_cart", "work", "apartment", "monitoring", "storefront", "newspaper", "history", "tune", "link",
  "language", "filter_list", "sort", "visibility", "visibility_off", "keyboard_arrow_down", "keyboard_arrow_up", "task_alt",
  "pending", "image", "description", "sync", "travel_explore", "rocket_launch", "list_alt", "dataset",
  "view_column", "analytics", "shield", "key", "public", "cancel", "help", "lightbulb", "send", "add_circle",
  "radio_button_unchecked", "account_circle", "restart_alt", "fact_check", "query_stats", "build"];
const FILLED = ["home", "folder", "table_chart", "settings", "smart_toy", "wand_stars", "check_circle", "error",
  "warning", "info", "account_circle"];
const read = (name, fill) => {
  const svg = readFileSync(mod(`@material-symbols/svg-400/rounded/${name}${fill ? "-fill" : ""}.svg`), "utf8");
  return [...svg.matchAll(/ d="([^"]+)"/g)].map(m => m[1]).join(" ");
};
const paths = {};
for (const name of ICONS) paths[name] = read(name, false);
for (const name of FILLED) paths[name + "-fill"] = read(name, true);
writeFileSync(join(UI, "public", "js", "icons.js"),
`// Material Symbols (Rounded, weight 400), Google, Apache License 2.0. Generated by scripts/build_ui_assets.mjs:
// add a name to ICONS there and run it again. Every path uses the viewBox "0 -960 960 960".
const MATERIAL_ICONS = ${JSON.stringify(paths, null, 0).replace(/","/g, '",\n"')};
`);
console.log(`wrote frontend/public/js/icons.js (${ICONS.length} icons + ${FILLED.length} filled)`);
