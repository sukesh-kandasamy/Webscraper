// Universal Scraper UI: what every page shares (Alpine.js). Each page adds its own script in js/pages/.

// Apply the saved theme straight away so the page doesn't flash the wrong colours.
function applyTheme(theme) {
  const dark = theme === "dark" || (theme !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}
applyTheme((() => { try { return JSON.parse(localStorage.getItem("us:theme")) || "system"; } catch { return "system"; } })());

// Heroicons (MIT), 20px solid. Static strings only; rendered with x-html.
const ICON_PATHS = {
  home: "M10.7 2.3a1 1 0 0 0-1.4 0l-7 7A1 1 0 0 0 3 11h1v6a1 1 0 0 0 1 1h3v-4h4v4h3a1 1 0 0 0 1-1v-6h1a1 1 0 0 0 .7-1.7l-7-7Z",
  plus: "M10.75 4.75a.75.75 0 0 0-1.5 0v4.5h-4.5a.75.75 0 0 0 0 1.5h4.5v4.5a.75.75 0 0 0 1.5 0v-4.5h4.5a.75.75 0 0 0 0-1.5h-4.5v-4.5Z",
  folder: "M3.75 3A1.75 1.75 0 0 0 2 4.75v3.26a3.24 3.24 0 0 1 1.75-.51h12.5c.64 0 1.24.19 1.75.51V6.75A1.75 1.75 0 0 0 16.25 5h-4.84a.25.25 0 0 1-.18-.07L9.82 3.51A1.75 1.75 0 0 0 8.59 3H3.75ZM3.75 9A1.75 1.75 0 0 0 2 10.75v4.5c0 .97.78 1.75 1.75 1.75h12.5c.97 0 1.75-.78 1.75-1.75v-4.5A1.75 1.75 0 0 0 16.25 9H3.75Z",
  table: "M.99 5.24A2.25 2.25 0 0 1 3.25 3h13.5A2.25 2.25 0 0 1 19 5.25l.01 9.5A2.25 2.25 0 0 1 16.76 17H3.26A2.27 2.27 0 0 1 1 14.75l-.01-9.51Zm8.26 9.52v-.63a.75.75 0 0 0-.75-.75H3.24a.75.75 0 0 0-.75.75v.63c0 .41.34.74.75.74h5.27c.41 0 .74-.33.74-.74Zm1.5 0c0 .41.33.74.75.74h5.25a.75.75 0 0 0 .75-.74v-.63a.75.75 0 0 0-.75-.75H11.5a.75.75 0 0 0-.75.75v.63Zm6.75-3.63v-.62a.75.75 0 0 0-.75-.75H11.5a.75.75 0 0 0-.75.75v.62c0 .42.33.75.75.75h5.25c.42 0 .75-.33.75-.75Zm-8.25 0v-.62a.75.75 0 0 0-.75-.75H3.24a.75.75 0 0 0-.75.75v.62c0 .42.34.75.75.75H8.5c.42 0 .75-.33.75-.75ZM17.5 7.5v-.62a.75.75 0 0 0-.75-.75H11.5a.75.75 0 0 0-.75.75v.62c0 .42.33.75.75.75h5.25c.42 0 .75-.33.75-.75Zm-8.25 0v-.62a.75.75 0 0 0-.75-.75H3.24a.75.75 0 0 0-.75.75v.62c0 .42.34.75.75.75H8.5c.42 0 .75-.33.75-.75Z",
  cog: "M7.84 1.8A1 1 0 0 1 8.82 1h2.36a1 1 0 0 1 .98.8l.28 1.42c.38.15.74.36 1.07.6l1.37-.47a1 1 0 0 1 1.19.45l1.18 2.04a1 1 0 0 1-.21 1.25l-1.09.95a6 6 0 0 1 0 1.92l1.09.95a1 1 0 0 1 .21 1.25l-1.18 2.04a1 1 0 0 1-1.19.45l-1.37-.47c-.33.24-.69.45-1.07.6l-.28 1.42a1 1 0 0 1-.98.8H8.82a1 1 0 0 1-.98-.8l-.28-1.42a6 6 0 0 1-1.07-.6l-1.37.47a1 1 0 0 1-1.19-.45L2.75 12.2a1 1 0 0 1 .21-1.25l1.09-.95a6 6 0 0 1 0-1.92l-1.09-.95a1 1 0 0 1-.21-1.25l1.18-2.04a1 1 0 0 1 1.19-.45l1.37.47c.33-.24.69-.45 1.07-.6l.28-1.42ZM10 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
  cart: "M1 1.75A.75.75 0 0 1 1.75 1h1.63c.84 0 1.55.6 1.71 1.42L5.3 3.5h11.2a.75.75 0 0 1 .73.92l-1.2 5.25a1.75 1.75 0 0 1-1.7 1.36H7.07l.24 1.22c.02.12.13.2.25.2h8.69a.75.75 0 0 1 0 1.5H7.56a1.75 1.75 0 0 1-1.72-1.42L3.62 2.7a.25.25 0 0 0-.25-.2H1.75A.75.75 0 0 1 1 1.75ZM6 17.5a1.5 1.5 0 1 1 3 0 1.5 1.5 0 0 1-3 0ZM15.5 16a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z",
  briefcase: "M6 3.75A2.75 2.75 0 0 1 8.75 1h2.5A2.75 2.75 0 0 1 14 3.75v.44c.79.07 1.57.18 2.35.31A2.24 2.24 0 0 1 18 6.71v3.03a2.75 2.75 0 0 1-1.96 2.64A23.6 23.6 0 0 1 10 13.25c-2.1 0-4.13-.3-6.04-.87A2.75 2.75 0 0 1 2 9.74V6.71c0-1.1.79-2.02 1.65-2.21.78-.13 1.56-.24 2.35-.31v-.44ZM12.5 3.75v.32a41.3 41.3 0 0 0-5 0v-.32c0-.69.56-1.25 1.25-1.25h2.5c.69 0 1.25.56 1.25 1.25ZM10 10a1 1 0 0 0-1 1v.01a1 1 0 0 0 1 1h.01a1 1 0 0 0 1-1V11a1 1 0 0 0-1-1H10ZM3 13.04v2.25c0 1.1.79 2.02 1.65 2.21 1.74.29 3.53.44 5.35.44s3.61-.15 5.35-.44A2.24 2.24 0 0 0 17 15.29v-2.25c-.25.11-.5.2-.77.28A25.1 25.1 0 0 1 10 14.75c-2.16 0-4.26-.3-6.23-.86A3.3 3.3 0 0 1 3 13.04Z",
  building: "M4 16.5v-13h-.25a.75.75 0 0 1 0-1.5h12.5a.75.75 0 0 1 0 1.5H16v13h.25a.75.75 0 0 1 0 1.5h-3.5a.75.75 0 0 1-.75-.75v-2.5a.75.75 0 0 0-.75-.75h-2.5a.75.75 0 0 0-.75.75v2.5a.75.75 0 0 1-.75.75h-3.5a.75.75 0 0 1 0-1.5H4Zm3-11a.5.5 0 0 1 .5-.5h1a.5.5 0 0 1 .5.5v1a.5.5 0 0 1-.5.5h-1a.5.5 0 0 1-.5-.5v-1ZM7.5 9a.5.5 0 0 0-.5.5v1a.5.5 0 0 0 .5.5h1a.5.5 0 0 0 .5-.5v-1a.5.5 0 0 0-.5-.5h-1ZM11 5.5a.5.5 0 0 1 .5-.5h1a.5.5 0 0 1 .5.5v1a.5.5 0 0 1-.5.5h-1a.5.5 0 0 1-.5-.5v-1Zm.5 3.5a.5.5 0 0 0-.5.5v1a.5.5 0 0 0 .5.5h1a.5.5 0 0 0 .5-.5v-1a.5.5 0 0 0-.5-.5h-1Z",
  house: "M14.5 9V5.25a.75.75 0 0 0-.75-.75h-1.5a.75.75 0 0 0-.75.75v1.29L10.7 5.3a1 1 0 0 0-1.4 0l-7 7A1 1 0 0 0 3 14h1v3a1 1 0 0 0 1 1h3.5v-4h3v4H15a1 1 0 0 0 1-1v-3h1a1 1 0 0 0 .7-1.7L14.5 9Z",
  chart: "M15.5 2A1.5 1.5 0 0 0 14 3.5v13a1.5 1.5 0 0 0 1.5 1.5h1a1.5 1.5 0 0 0 1.5-1.5v-13A1.5 1.5 0 0 0 16.5 2h-1ZM9.5 6A1.5 1.5 0 0 0 8 7.5v9A1.5 1.5 0 0 0 9.5 18h1a1.5 1.5 0 0 0 1.5-1.5v-9A1.5 1.5 0 0 0 10.5 6h-1ZM3.5 10A1.5 1.5 0 0 0 2 11.5v5A1.5 1.5 0 0 0 3.5 18h1A1.5 1.5 0 0 0 6 16.5v-5A1.5 1.5 0 0 0 4.5 10h-1Z",
  news: "M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v11.75A2.75 2.75 0 0 0 16.75 18h-12A2.75 2.75 0 0 1 2 15.25V3.5Zm3.75 7a.75.75 0 0 0 0 1.5h4.5a.75.75 0 0 0 0-1.5h-4.5Zm0 3a.75.75 0 0 0 0 1.5h4.5a.75.75 0 0 0 0-1.5h-4.5ZM5 5.75A.75.75 0 0 1 5.75 5h4.5a.75.75 0 0 1 .75.75v2.5a.75.75 0 0 1-.75.75h-4.5A.75.75 0 0 1 5 8.25v-2.5ZM15.5 6v9.25a1.25 1.25 0 0 0 2.5 0V7a1 1 0 0 0-1-1h-1.5Z",
  sparkles: "M15.98 1.8a.75.75 0 0 0-1.46 0l-.26 1.03a2.25 2.25 0 0 1-1.64 1.64l-1.02.26a.75.75 0 0 0 0 1.45l1.02.26c.8.2 1.43.84 1.64 1.64l.26 1.03a.75.75 0 0 0 1.46 0l.26-1.03c.2-.8.84-1.43 1.64-1.64l1.03-.26a.75.75 0 0 0 0-1.45l-1.03-.26a2.25 2.25 0 0 1-1.64-1.64l-.26-1.03ZM6.95 5.3a.75.75 0 0 0-1.4 0L4.7 7.84a1.75 1.75 0 0 1-1.1 1.1l-2.55.85a.75.75 0 0 0 0 1.42l2.55.85c.52.17.93.58 1.1 1.1l.85 2.55a.75.75 0 0 0 1.42 0l.85-2.55c.17-.52.58-.93 1.1-1.1l2.55-.85a.75.75 0 0 0 0-1.42l-2.55-.85a1.75 1.75 0 0 1-1.1-1.1L6.95 5.3Z",
  search: "M9 3.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11ZM2 9a7 7 0 1 1 12.45 4.39l3.08 3.08a.75.75 0 1 1-1.06 1.06l-3.08-3.08A7 7 0 0 1 2 9Z",
  download: "M10.75 2.75a.75.75 0 0 0-1.5 0v8.61L6.3 8.24a.75.75 0 1 0-1.1 1.02l4.25 4.5a.75.75 0 0 0 1.1 0l4.25-4.5a.75.75 0 0 0-1.1-1.02l-2.95 3.12V2.75ZM3.5 12.75a.75.75 0 0 0-1.5 0v2.5A2.75 2.75 0 0 0 4.75 18h10.5A2.75 2.75 0 0 0 18 15.25v-2.5a.75.75 0 0 0-1.5 0v2.5c0 .69-.56 1.25-1.25 1.25H4.75c-.69 0-1.25-.56-1.25-1.25v-2.5Z",
  play: "M6.3 2.84A1.5 1.5 0 0 0 4 4.11v11.78a1.5 1.5 0 0 0 2.3 1.27l9.34-5.89a1.5 1.5 0 0 0 0-2.54L6.3 2.84Z",
  refresh: "M15.31 11.42a5.5 5.5 0 0 1-9.2 2.47l-.31-.31h2.43a.75.75 0 0 0 0-1.5H3.99a.75.75 0 0 0-.75.75v4.24a.75.75 0 0 0 1.5 0v-2.43l.31.31a7 7 0 0 0 11.71-3.14.75.75 0 0 0-1.45-.39Zm1.23-3.72a.75.75 0 0 0 .22-.53V2.93a.75.75 0 0 0-1.5 0v2.43l-.31-.31A7 7 0 0 0 3.24 8.19a.75.75 0 0 0 1.45.39 5.5 5.5 0 0 1 9.2-2.47l.31.31h-2.43a.75.75 0 0 0 0 1.5h4.24a.75.75 0 0 0 .53-.22Z",
  pencil: "M2.7 15.08 1.97 17.4a.5.5 0 0 0 .62.62l2.33-.72a4 4 0 0 0 1.62-.98l8.2-8.2a2 2 0 0 0-2.83-2.83l-8.2 8.2a4 4 0 0 0-1 1.6ZM13.85 3.73a2 2 0 0 1 2.83 2.83l-.48.48-2.83-2.83.48-.48Z",
  trash: "M8.75 1A2.75 2.75 0 0 0 6 3.75v.44c-.8.08-1.57.18-2.35.32a.75.75 0 1 0 .26 1.48l.15-.03.85 10.66A2.75 2.75 0 0 0 7.65 19h4.7a2.75 2.75 0 0 0 2.74-2.53l.85-10.66.15.03a.75.75 0 1 0 .26-1.48A41 41 0 0 0 14 4.19v-.44A2.75 2.75 0 0 0 11.25 1h-2.5ZM10 4c.84 0 1.67.03 2.5.08v-.33c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.33C8.33 4.03 9.16 4 10 4ZM8.58 7.72a.75.75 0 0 0-1.5.06l.3 7.5a.75.75 0 1 0 1.5-.06l-.3-7.5Zm4.34.06a.75.75 0 1 0-1.5-.06l-.3 7.5a.75.75 0 1 0 1.5.06l.3-7.5Z",
  copy: "M7 3.5A1.5 1.5 0 0 1 8.5 2h3.88a1.5 1.5 0 0 1 1.06.44l3.12 3.12A1.5 1.5 0 0 1 17 6.62V12.5a1.5 1.5 0 0 1-1.5 1.5h-1v-3.38a3 3 0 0 0-.88-2.12l-3.12-3.12A3 3 0 0 0 8.38 4.5H7v-1Z M4.5 6A1.5 1.5 0 0 0 3 7.5v9A1.5 1.5 0 0 0 4.5 18h7a1.5 1.5 0 0 0 1.5-1.5v-5.88a1.5 1.5 0 0 0-.44-1.06L9.44 6.44A1.5 1.5 0 0 0 8.38 6H4.5Z",
  stop: "M5.25 3A2.25 2.25 0 0 0 3 5.25v9.5A2.25 2.25 0 0 0 5.25 17h9.5A2.25 2.25 0 0 0 17 14.75v-9.5A2.25 2.25 0 0 0 14.75 3h-9.5Z",
  external: "M4.25 5.5a.75.75 0 0 0-.75.75v8.5c0 .41.34.75.75.75h8.5a.75.75 0 0 0 .75-.75v-4a.75.75 0 0 1 1.5 0v4A2.25 2.25 0 0 1 12.75 17h-8.5A2.25 2.25 0 0 1 2 14.75v-8.5A2.25 2.25 0 0 1 4.25 4h5a.75.75 0 0 1 0 1.5h-5Zm6.5-3.25a.75.75 0 0 1 .75-.75h5.75a.75.75 0 0 1 .75.75v5.75a.75.75 0 0 1-1.5 0V4.06l-6.22 6.22a.75.75 0 1 1-1.06-1.06L14.94 3h-3.44a.75.75 0 0 1-.75-.75Z",
  dots: "M3 10a1.5 1.5 0 1 1 3 0 1.5 1.5 0 0 1-3 0ZM8.5 10a1.5 1.5 0 1 1 3 0 1.5 1.5 0 0 1-3 0ZM15.5 8.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z",
  check: "M16.7 4.15a.75.75 0 0 1 .15 1.05l-8 10.5a.75.75 0 0 1-1.13.08l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.9 3.89 7.48-9.82a.75.75 0 0 1 1.05-.14Z",
  warn: "M8.49 2.86a1.75 1.75 0 0 1 3.02 0l6.28 10.88A1.75 1.75 0 0 1 16.28 16.4H3.72a1.75 1.75 0 0 1-1.51-2.66L8.49 2.86ZM10 7a.75.75 0 0 1 .75.75v3a.75.75 0 0 1-1.5 0v-3A.75.75 0 0 1 10 7Zm0 7a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z",
  info: "M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Zm-7-4a1 1 0 1 1-2 0 1 1 0 0 1 2 0ZM9 9a.75.75 0 0 0 0 1.5h.25a.25.25 0 0 1 .24.3l-.45 2.08A1.75 1.75 0 0 0 10.75 15H11a.75.75 0 0 0 0-1.5h-.25a.25.25 0 0 1-.24-.3l.45-2.08A1.75 1.75 0 0 0 9.25 9H9Z",
  error: "M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Zm-8-5a.75.75 0 0 1 .75.75v4.5a.75.75 0 0 1-1.5 0v-4.5A.75.75 0 0 1 10 5Zm0 10a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z",
  sun: "M10 2a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5A.75.75 0 0 1 10 2ZM10 15a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5A.75.75 0 0 1 10 15ZM10 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM15.66 5.4a.75.75 0 0 0-1.06-1.06l-1.06 1.06a.75.75 0 0 0 1.06 1.06l1.06-1.06ZM6.46 14.6a.75.75 0 1 0-1.06-1.06l-1.06 1.06a.75.75 0 0 0 1.06 1.06l1.06-1.06ZM18 10a.75.75 0 0 1-.75.75h-1.5a.75.75 0 0 1 0-1.5h1.5A.75.75 0 0 1 18 10ZM5 10a.75.75 0 0 1-.75.75h-1.5a.75.75 0 0 1 0-1.5h1.5A.75.75 0 0 1 5 10ZM14.6 15.66a.75.75 0 0 0 1.06-1.06l-1.06-1.06a.75.75 0 1 0-1.06 1.06l1.06 1.06ZM5.4 6.46a.75.75 0 0 0 1.06-1.06L5.4 4.34a.75.75 0 0 0-1.06 1.06L5.4 6.46Z",
  moon: "M7.46 1.61a.75.75 0 0 1 .17.81 6 6 0 0 0 7.95 7.95.75.75 0 0 1 .98.98A7.5 7.5 0 1 1 6.65 1.44a.75.75 0 0 1 .81.17Z",
  monitor: "M2 4.25A2.25 2.25 0 0 1 4.25 2h11.5A2.25 2.25 0 0 1 18 4.25v8.5A2.25 2.25 0 0 1 15.75 15h-3.11l.22 1.32a.75.75 0 0 1-.74.68H7.88a.75.75 0 0 1-.74-.68l.22-1.32H4.25A2.25 2.25 0 0 1 2 12.75v-8.5Zm1.5 0v7.5c0 .41.34.75.75.75h11.5a.75.75 0 0 0 .75-.75v-7.5a.75.75 0 0 0-.75-.75H4.25a.75.75 0 0 0-.75.75Z",
  menu: "M2 4.75A.75.75 0 0 1 2.75 4h14.5a.75.75 0 0 1 0 1.5H2.75A.75.75 0 0 1 2 4.75Zm0 5A.75.75 0 0 1 2.75 9h14.5a.75.75 0 0 1 0 1.5H2.75A.75.75 0 0 1 2 9.75Zm0 5a.75.75 0 0 1 .75-.75h14.5a.75.75 0 0 1 0 1.5H2.75a.75.75 0 0 1-.75-.75Z",
  x: "M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z",
  chevron: "M7.2 14.8a.75.75 0 0 1 0-1.06L10.94 10 7.2 6.27a.75.75 0 1 1 1.06-1.06l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0Z",
  left: "M12.8 5.2a.75.75 0 0 1 0 1.06L9.06 10l3.74 3.74a.75.75 0 1 1-1.06 1.06l-4.27-4.27a.75.75 0 0 1 0-1.06l4.27-4.27a.75.75 0 0 1 1.06 0Z",
  bolt: "M11.98 1.58a.75.75 0 0 0-1.33-.48l-7.5 9.5A.75.75 0 0 0 3.75 12H9.5l-1.48 6.42a.75.75 0 0 0 1.33.48l7.5-9.5A.75.75 0 0 0 16.25 8H10.5l1.48-6.42Z",
  rows: "M2 4.75A.75.75 0 0 1 2.75 4h14.5a.75.75 0 0 1 0 1.5H2.75A.75.75 0 0 1 2 4.75Zm0 10.5a.75.75 0 0 1 .75-.75h7.5a.75.75 0 0 1 0 1.5h-7.5a.75.75 0 0 1-.75-.75ZM2 10a.75.75 0 0 1 .75-.75h14.5a.75.75 0 0 1 0 1.5H2.75A.75.75 0 0 1 2 10Z",
  clock: "M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm.75-13a.75.75 0 0 0-1.5 0v5c0 .41.34.75.75.75h4a.75.75 0 0 0 0-1.5h-3.25V5Z",
  lock: "M10 1a4.5 4.5 0 0 0-4.5 4.5V9H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2h-.5V5.5A4.5 4.5 0 0 0 10 1Zm3 8V5.5a3 3 0 1 0-6 0V9h6Z",
};
function iconSvg(name, cls = "h-5 w-5") {
  const d = ICON_PATHS[name] || ICON_PATHS.info;
  return `<svg class="${cls}" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="${d}"/></svg>`;
}

async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    const err = new Error("The app's server is not responding. Is it still running?");
    err.status = 0;
    throw err;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail
      : data.detail?.message || (Array.isArray(data.detail) ? "Some input is not valid." : `Request failed (${res.status})`));
    err.detail = data.detail;
    err.status = res.status;
    throw err;
  }
  return data;
}

const TEMPLATES = [
  { id: "products", icon: "cart", name: "Products & prices", hint: "Shops, marketplaces, B2B catalogues",
    columns: ["name", "price", "rating", "reviews", "seller", "url"],
    prompt: "I need the product name, image, price, rating and number of reviews of each product." },
  { id: "jobs", icon: "briefcase", name: "Job listings", hint: "Job boards, career pages",
    columns: ["title", "company", "location", "salary", "experience", "posted", "url"],
    prompt: "I need the job title, company, location, salary, experience and posted date of each job." },
  { id: "real_estate", icon: "house", name: "Real estate", hint: "Flats, houses, rentals",
    columns: ["title", "price", "location", "area_sqft", "bedrooms", "url"],
    prompt: "I need the title, price, location, area in sq.ft and number of bedrooms of each property." },
  { id: "stocks", icon: "chart", name: "Stock & market tables", hint: "Tickers, prices, indices",
    columns: ["symbol", "name", "price", "change", "change_percent", "volume"],
    prompt: "I need the symbol, company name, price, change, change % and volume of each stock." },
  { id: "directory", icon: "building", name: "Business directory", hint: "Suppliers, companies, shops",
    columns: ["name", "category", "location", "rating", "url"],
    prompt: "I need the business name, category, location and rating of each listing." },
  { id: "news", icon: "news", name: "News & articles", hint: "Headlines, blogs, press",
    columns: ["title", "summary", "published", "url"],
    prompt: "I need the headline, summary and published date of each article." },
  { id: "auto", icon: "sparkles", name: "Anything else", hint: "Let the AI decide the columns", columns: [],
    prompt: "Collect all the important details of each item." },
];

const AGENT_EXAMPLES = [
  { label: "Mystery books", site: "books.toscrape.com",
    goal: "Mystery books: title, price, rating and availability. Only books under £20, all pages." },
  { label: "Quotes by tag", site: "quotes.toscrape.com",
    goal: "Quotes tagged 'inspirational': the quote text, author and tags." },
  { label: "Remote jobs", site: "weworkremotely.com",
    goal: "Remote programming jobs: job title, company, region and posted date. Newest first." },
];

const TYPE_LABELS = { money: "price", number: "number", percent: "%", rating: "rating", date: "date", url: "link", image: "image" };
const OP_LABELS = { lt: "<", lte: "≤", gt: ">", gte: "≥", eq: "=", ne: "≠", contains: "contains", not_contains: "doesn't contain" };

const PAGE_TYPES = {
  ecommerce: "Products & prices", jobs: "Job listings", real_estate: "Real estate",
  stocks_finance: "Stock & market table", directory: "Business directory", news_articles: "News & articles",
  events: "Events", other: "Listing",
};

const EVENT_LABELS = {
  fetch: "Opened a page", job_started: "Scrape started", job_paused: "Scrape paused", job_blocked: "Scrape stopped by site",
  job_deleted: "Run deleted", export: "Downloaded results", session_saved: "Login saved",
  session_forgotten: "Login removed", login_confirmed: "Own account confirmed", settings_changed: "Site settings changed",
  cooldown: "Site paused for a while", cooldown_cleared: "Site pause ended", challenge_solved: "Human check solved by you",
  window_opened: "Browser window opened", recipe_built: "AI set up the columns", recipe_saved: "Column setup saved",
  retention_purge: "Old data cleaned up", site_data_deleted: "Site data deleted", retry_after: "Waited as the site asked",
  scrape_saved: "Scrape saved", scrape_deleted: "Scrape deleted", detail_recipe_built: "AI set up item pages",
  scrape_repaired: "Columns re-detected", agent_planned: "Agent made a plan", agent_started: "Agent started",
  agent_refused: "Agent action refused", agent_handoff: "Agent handed results over", agent_finished: "Agent stopped",
  agent_rerecord: "Agent re-recording a route", agent_fix: "Agent fixed a problem",
};

function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem("us:" + key));
    localStorage.setItem("us:" + key, JSON.stringify(value));
  } catch { return undefined; }
}

// A message to show on the next page (after an action that moves to another address).
function flash(msg, type = "success") {
  try { sessionStorage.setItem("us:flash", JSON.stringify({ msg, type })); } catch { /* private mode */ }
}
function takeFlash() {
  try {
    const f = JSON.parse(sessionStorage.getItem("us:flash"));
    sessionStorage.removeItem("us:flash");
    return f;
  } catch { return null; }
}
function goTo(path, msg) { if (msg) flash(msg); window.location.href = path; }

const PATHS = { home: "/", new: "/scrapes/new", scrapes: "/scrapes", runs: "/results", results: "/results",
                settings: "/settings", agent: "/agent" };

// Shared by every page: theme, dialogs, toasts, status, error messages, formatting, the visible browser
// window for logins and human checks. Each page adds its own state and methods (see page()).
function shell() {
  return {
    theme: store("theme") || "system",
    toasts: [],
    dialog: null,                // {title, message, input, value, confirmText, danger}
    menuOpen: false,
    advanced: store("advanced") === true,
    agentShown: store("agentShown") !== false,
    status: null,
    error: null,                 // {title, hint, raw}
    busy: "",
    jobs: [],                    // recent runs (pages that show them, and the needs-you banner)
    usesJobs: false,             // a page sets this to keep the run list fresh
    blockedPage: null,           // {domain, url} of a page that asked for a human check
    win: null,                   // the visible browser window (login / solve)
    winTimer: null,
    statusTimer: null,
    pollTimer: null,
    templates: TEMPLATES,
    data: document.body.dataset, // page data from the server (ids, active page)

    async initShell() {
      await this.loadStatus();
      this.$watch("advanced", v => store("advanced", v));
      this.$watch("agentShown", v => store("agentShown", v));
      this.$watch("theme", v => { store("theme", v); applyTheme(v); });
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(this.theme));
      document.addEventListener("keydown", e => {
        if (e.key === "Escape" && this.dialog) this.closeDialog(false);
        if (e.key === "Escape") { this.menuOpen = false; this.colMenu = false; }
      });
      const f = takeFlash();
      if (f) this.toast(f.msg, f.type);
      if (this.usesJobs || this.status?.needs_you?.length) await this.loadJobs();
    },

    // Status (counters for the navigation) every few seconds, faster while something runs.
    async loadStatus() {
      try { this.status = await api("GET", "/api/status"); } catch (e) { if (!this.status) this.showError(e); }
      const busy = this.status && (this.status.active_jobs || this.status.active_agent_runs);
      clearTimeout(this.statusTimer);
      this.statusTimer = setTimeout(async () => {
        await this.loadStatus();
        if (!this.usesJobs && this.status?.needs_you?.length && !this.pollTimer) this.loadJobs();
      }, busy ? 3000 : 10000);
    },

    async loadJobs() {
      try { this.jobs = await api("GET", "/api/jobs?limit=100"); } catch { /* server may be restarting */ }
      clearTimeout(this.pollTimer);
      const keepPolling = this.usesJobs || this.needsYouJobs().length;
      this.pollTimer = keepPolling ? setTimeout(() => this.loadJobs(), this.activeJobs().length ? 2000 : 8000) : null;
      if (this.afterJobs) await this.afterJobs();
    },

    // ---------------------------------------------------------------- helpers
    icon(name, cls) { return iconSvg(name, cls); },
    go(view) { goTo(PATHS[view] || "/"); },
    cycleTheme() { this.theme = { system: "light", light: "dark", dark: "system" }[this.theme] || "system"; },
    themeIcon() { return { system: "monitor", light: "sun", dark: "moon" }[this.theme]; },
    themeLabel() { return { system: "System theme", light: "Light theme", dark: "Dark theme" }[this.theme]; },
    agentEnabled() { return !!this.status?.agent?.enabled && this.agentShown; },

    // Styled dialogs instead of the browser's confirm() / prompt().
    ask(opts) {
      return new Promise(resolve => {
        this.dialog = { title: "", message: "", input: false, value: "", confirmText: "OK", danger: false, ...opts, resolve };
        this.$nextTick(() => document.getElementById(this.dialog.input ? "dialog-input" : "dialog-ok")?.focus());
      });
    },
    confirmDialog(title, message, confirmText = "Delete", danger = true) {
      return this.ask({ title, message, confirmText, danger });
    },
    promptDialog(title, label, value = "", confirmText = "Save") {
      return this.ask({ title, message: label, input: true, value, confirmText });
    },
    closeDialog(ok) {
      const d = this.dialog;
      if (!d) return;
      this.dialog = null;
      d.resolve(ok ? (d.input ? (d.value || "").trim() : true) : (d.input ? null : false));
    },
    toast(msg, type = "success") {
      const id = Date.now() + Math.random();
      this.toasts.push({ id, msg, type });
      setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, 5500);
    },

    fmt(n) { return (n || 0).toLocaleString(); },
    hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } },
    isUrl(v) { return typeof v === "string" && /^https?:\/\//.test(v); },
    isImage(v) { return this.isUrl(v) && /\.(jpe?g|png|webp|gif|avif)(\?|$)/i.test(v); },
    pageTypeLabel(t) { return PAGE_TYPES[t] || ""; },
    eventLabel(e) { return EVENT_LABELS[e] || e; },
    typeLabel(t) { return TYPE_LABELS[t] || ""; },
    filterLabel(f) { return `${this.pretty(f.column)} ${OP_LABELS[f.op] || f.op} ${f.value}`; },
    statusChip(s) {
      return { done: "chip-green", running: "chip-blue", queued: "chip-amber", stopped: "chip-gray",
               blocked: "chip-red", error: "chip-red", needs_you: "chip-amber" }[s] || "chip-gray";
    },
    statusLabel(s) {
      return { done: "Finished", running: "Collecting", queued: "Waiting", stopped: "Stopped",
               blocked: "Stopped by site", error: "Failed", needs_you: "Needs you" }[s] || s;
    },
    timeAgo(ts) {
      if (!ts) return "";
      const s = Math.max(0, Date.now() / 1000 - ts);
      if (s < 60) return "just now";
      if (s < 3600) return Math.round(s / 60) + " min ago";
      if (s < 86400) return Math.round(s / 3600) + " h ago";
      return Math.round(s / 86400) + " d ago";
    },
    columns(rows) {
      const cols = [];
      for (const r of rows || []) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
      return cols;
    },
    pretty(col) { return col.replace(/_/g, " "); },
    cellText(v) {
      if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
      return v;
    },
    shortText(v) { const s = String(this.cellText(v) ?? ""); return s.length > 40 ? s.slice(0, 38) + "…" : s; },
    needsYouJobs() { return this.jobs.filter(j => j.status === "needs_you"); },
    activeJobs() { return this.jobs.filter(j => ["queued", "running"].includes(j.status)); },
    jobTitle(j) { return j.scrape_name || this.hostOf(j.urls?.[0]) || ("Run #" + j.id); },
    progressPct(j) {
      if (!j) return 0;
      if (j.status === "done") return 100;
      return j.pages_total ? Math.max(5, Math.round(100 * j.pages_done / j.pages_total)) : 5;
    },

    // Turn API errors into a title and a "what to do" hint.
    explain(e) {
      const msg = e.message || "";
      if (e.status === 0) return { title: "Can't reach the app", hint: "Start it again with  python run.py  and refresh this page." };
      if (/robots\.txt/i.test(msg)) return { title: "This page is off-limits for scrapers",
        hint: "The site's crawling rules block it (search pages often are). Try a category or listing page from the same site.", raw: msg };
      if (/CERTIFICATE|SSL/i.test(msg)) return { title: "Your network blocked this site",
        hint: "A company firewall is intercepting the connection. Try another network, or ask IT to allow the site.", raw: msg };
      if (e.status === 429 || /budget/i.test(msg)) return { title: "Daily limit reached for this site",
        hint: "It resets tomorrow. You can raise the limit in Settings → Sites (turn on Advanced mode in Settings).", raw: msg };
      if (/cooling down/i.test(msg)) return { title: "This site is paused for a while",
        hint: "It recently limited our visits. Wait a bit, or end the pause in Settings → Sites.", raw: msg };
      if (/needs a login/i.test(msg)) return { title: "This page needs a login",
        hint: "If it's your own account, use Settings → Sites → Log in, then try again.", raw: msg };
      if (/too large/i.test(msg)) return { title: "This page is too big for the AI",
        hint: "Try a page with fewer items, or set up the columns by hand (turn on Advanced mode in Settings).", raw: msg };
      if (/repeated listing|Couldn't find any repeated/i.test(msg)) return { title: "No list of items found",
        hint: "Use a page that shows many similar items (search results, a category, a table). Single product pages won't work.", raw: msg };
      if (/no AI|AI is turned off|API key/i.test(msg)) return { title: "The AI is not available",
        hint: "Add an AI key to .env, or set up the columns by hand (turn on Advanced mode in Settings).", raw: msg };
      if (/rate-limited/i.test(msg)) {
        const m = msg.match(/wait (about \d+ minutes|\d+ seconds)/);
        return { title: "The AI is busy", hint: m ? `Its provider asks to wait ${m[1]}. Try again after that.` : "Wait a minute and try again.", raw: msg };
      }
      if (e.status === 502) return { title: "Couldn't open that page", hint: "Check the link in your browser. The site may be down or blocking automated visits.", raw: msg };
      if (e.status === 400 && /local|private|http\(s\)/i.test(msg)) return { title: "That link can't be used", hint: msg };
      return { title: "Something went wrong", hint: msg };
    },
    showError(e) { this.error = this.explain(e); },
    handleError(e) {
      const d = e.detail || {};
      if (e.status === 409 && d.code === "needs_human") { this.blockedPage = d; this.error = null; return; }
      this.showError(e);
    },

    // ---------------------------------------------------------------- actions that open another page
    startWizard(template = null, url = "") {
      const q = new URLSearchParams();
      if (template?.id) q.set("template", template.id);
      if (url) q.set("url", url);
      goTo("/scrapes/new" + (q.toString() ? "?" + q : ""));
    },
    trySample() { this.startWizard(TEMPLATES[0], "https://books.toscrape.com/"); },
    openJob(j) { if (j) goTo(`/results/${j.id}`); },
    openLatestRun(s) { if (s.last_run) goTo(`/results/${s.last_run.id}`); },
    editScrape(s) { goTo(`/scrapes/${s.id}/edit`); },
    async runScrape(s) {
      this.error = null;
      try {
        const { job_id } = await api("POST", `/api/scrapes/${s.id}/run`, {});
        goTo(`/results/${job_id}`, `Running “${s.name}” again.`);
      } catch (e) { this.handleError(e); }
    },
    async runAgainFromJob(j) {
      if (!j?.scrape_id) return;
      this.error = null;
      try {
        const { job_id } = await api("POST", `/api/scrapes/${j.scrape_id}/run`, {});
        goTo(`/results/${job_id}`, `Running “${this.jobTitle(j)}” again.`);
      } catch (e) { this.handleError(e); }
    },
    async rerecordScrape(scrapeId) {
      if (!scrapeId) return;
      this.error = null; this.busy = "agent-start";
      try {
        const run = await api("POST", `/api/agent/rerecord/${scrapeId}`, this.status?.agent?.limits);
        goTo(`/agent/runs/${run.id}`, "The agent is finding the way again. The saved scrape is updated when it's done.");
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    async stopJob(j) {
      await api("POST", `/api/jobs/${j.id}/stop`).catch(e => this.showError(e));
      this.loadJobs();
    },

    // ---------------------------------------------------------------- visible browser window
    async startLogin(site) {
      const url = await this.promptDialog(`Log in to ${site.domain}`, "Login page address", `https://${site.domain}/`, "Next");
      if (!url) return;
      if (!await this.confirmDialog("Is this your own account?", "Only continue if it is your own account and the site allows automated access while logged in. You'll type your password in a browser window; the app never sees it.", "Continue", false)) return;
      this.win = { mode: "login", domain: site.domain, url, status: "starting", message: "", account: site.session?.account || "" };
      try { this.trackWindow(`login:${site.domain}`, await api("POST", `/api/sites/${site.domain}/login`, { url, own_account: true })); }
      catch (e) { this.win = null; this.showError(e); }
    },
    async startSiteSolve() {
      const d = this.blockedPage;
      this.win = { mode: "site", domain: d.domain, url: d.url, status: "starting", message: "", account: "" };
      try { this.trackWindow(`site:${d.domain}`, await api("POST", `/api/sites/${d.domain}/solve`, { url: d.url })); }
      catch (e) { this.win = null; this.showError(e); }
    },
    async startJobSolve(j) {
      this.win = { mode: "job", jobId: j.id, domain: this.hostOf(j.paused_url), url: j.paused_url,
                   kind: j.paused_kind, status: "starting", message: "", account: "" };
      try { this.trackWindow(`job:${j.id}`, await api("POST", `/api/jobs/${j.id}/solve`)); }
      catch (e) { this.win = null; this.showError(e); }
    },
    trackWindow(key, w) {
      Object.assign(this.win, { key, status: w.status, message: w.message });
      clearTimeout(this.winTimer);
      const poll = async () => {
        if (!this.win || this.win.key !== key) return;
        try {
          const s = await api("GET", `/api/windows/${encodeURIComponent(key)}`);
          Object.assign(this.win, { status: s.status, message: s.message, title: s.title });
          if (["starting", "open"].includes(s.status)) this.winTimer = setTimeout(poll, 1500);
        } catch { /* window gone */ }
      };
      this.winTimer = setTimeout(poll, 1200);
    },
    async finishWindow() {
      const w = this.win;
      this.busy = "window";
      try {
        if (w.mode === "login") {
          const r = await api("POST", `/api/sites/${w.domain}/login/finish`, { account: w.account });
          this.toast(`Login saved for ${r.domain}. It's encrypted and lasts ${Math.round(r.expires_in_hours / 24)} days.` + (r.warning ? " " + r.warning : ""));
        } else if (w.mode === "site") {
          await api("POST", `/api/sites/${w.domain}/solve/finish`, { account: w.account });
          this.toast(`Thanks. ${w.domain} is open again; try once more.`);
          this.blockedPage = null;
        } else {
          await api("POST", `/api/jobs/${w.jobId}/solve/finish`, { account: w.account });
          this.toast("The scrape continues.");
        }
        this.win = null;
        await this.loadJobs();
        if (this.afterWindow) await this.afterWindow();
      } catch (e) { this.showError(e); }
      finally { this.busy = ""; }
    },
    async cancelWindow() {
      const w = this.win;
      this.win = null; clearTimeout(this.winTimer);
      if (w?.mode === "login") await api("POST", `/api/sites/${w.domain}/login/cancel`).catch(() => {});
    },
  };
}

// One page = the shared shell + that page's own state and methods. The page's start() runs after the
// shell has loaded the status. Getters are copied as getters.
function page(own) {
  const out = shell();
  Object.defineProperties(out, Object.getOwnPropertyDescriptors(own));
  out.init = async function () {
    await this.initShell();
    if (this.start) await this.start();
  };
  return out;
}
