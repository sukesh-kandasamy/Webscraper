// Universal Scraper UI (Alpine.js). One component drives the whole app shell.

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
  agent_rerecord: "Agent re-recording a route",
};

function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem("us:" + key));
    localStorage.setItem("us:" + key, JSON.stringify(value));
  } catch { return undefined; }
}

function scraper() {
  return {
    // ---- app shell ----
    view: "home",
    theme: store("theme") || "system",
    toasts: [],
    dialog: null,                // {title, message, input, value, confirmText, danger}
    loadStep: 0,
    loadTimer: null,
    scrapeQuery: "",
    page: 1,
    pageSize: 50,
    menuOpen: false,
    advanced: store("advanced") === true,
    status: null,
    error: null,                 // {title, hint, raw}
    busy: "",

    // ---- wizard ----
    step: 1,
    template: null,
    urlMain: "",
    urlsExtra: "",
    showExtra: false,
    fieldsText: "",
    autoColumns: true,
    prompt: "",
    request: null,               // what the AI understood from the prompt (editable)
    requestFor: "",              // the prompt text that request belongs to
    listMode: false,             // list columns instead of describing the data
    newReqColumn: "",
    allPages: false,
    analysis: null,
    recipeText: "",
    newColumn: "",
    scrapeName: "",
    maxPages: 3,
    editingScrapeId: null,
    personalChoice: {},
    personalPurpose: "",
    settings: {},
    blockedPage: null,

    // ---- data ----
    jobs: [],
    scrapes: [],
    sites: [],
    activity: [],

    // ---- results ----
    selectedJob: null,
    records: [],
    showPersonal: false,
    search: "",
    sortKey: "",
    sortAsc: true,
    hiddenCols: [],
    colMenu: false,
    pollTimer: null,
    resultTab: "data",

    // ---- agent mode ----
    agentShown: store("agentShown") !== false,
    agentGoal: "",
    agentSite: "",
    agentRun: null,              // the run on screen (with its steps)
    agentRuns: [],
    agentNewCol: "",
    agentLimits: { steps: 40, minutes: 10, ai_calls: 60 },
    agentTimer: null,
    agentExamples: AGENT_EXAMPLES,
    compare: null,
    compareError: "",
    compareView: "added",

    // ---- browser window dialog ----
    win: null,
    winTimer: null,

    templates: TEMPLATES,

    async init() {
      try {
        this.status = await api("GET", "/api/status");
        this.maxPages = Math.min(3, this.status.max_pages);
      } catch (e) { this.showError(e); }
      await Promise.all([this.loadJobs(), this.loadScrapes(), this.loadSites()]);
      this.$watch("advanced", v => store("advanced", v));
      this.$watch("agentShown", v => store("agentShown", v));
      if (this.status?.agent?.limits) this.agentLimits = { ...this.status.agent.limits };
      this.loadAgentRuns();
      this.$watch("theme", v => { store("theme", v); applyTheme(v); });
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(this.theme));
      this.$watch("busy", v => this.trackLoading(v));
      this.$watch("search", () => { this.page = 1; });
      document.addEventListener("keydown", e => {
        if (e.key === "Escape" && this.dialog) this.closeDialog(false);
        if (e.key === "Escape") { this.menuOpen = false; this.colMenu = false; }
      });
    },

    // ---------------------------------------------------------------- helpers
    icon(name, cls) { return iconSvg(name, cls); },
    viewTitle() {
      return { home: "Home", new: this.editingScrapeId ? "Edit scrape" : "New scrape", scrapes: "Saved scrapes",
               runs: "Results", settings: "Settings", agent: "Agent" }[this.view] || "";
    },
    cycleTheme() { this.theme = { system: "light", light: "dark", dark: "system" }[this.theme] || "system"; },
    themeIcon() { return { system: "monitor", light: "sun", dark: "moon" }[this.theme]; },
    themeLabel() { return { system: "System theme", light: "Light theme", dark: "Dark theme" }[this.theme]; },

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

    // Step-by-step messages while the page is being read, so waiting doesn't feel stuck.
    loadingSteps() {
      if (this.busy === "add") return ["Opening the page…", "Looking for the new column…", "Checking it on every item…"];
      if (this.busy === "details") return ["Opening the first item's page…", "Finding the extra details…", "Checking them…"];
      return ["Opening the page…", "Finding the list of items…",
              this.request ? "Matching the columns you described…" : "Choosing the important columns…", "Checking the result…"];
    },
    trackLoading(v) {
      clearInterval(this.loadTimer);
      this.loadStep = 0;
      if (["analyse", "ai", "add", "details"].includes(v)) {
        this.loadTimer = setInterval(() => { this.loadStep = Math.min(this.loadStep + 1, this.loadingSteps().length - 1); }, 2600);
      }
    },

    // Dashboard numbers.
    stats() {
      const now = Date.now() / 1000, day = 86400;
      const recent = this.jobs.filter(j => now - j.created_at < 7 * day);
      return {
        scrapes: this.scrapes.length,
        rowsWeek: recent.reduce((n, j) => n + (j.rows || 0), 0),
        runsWeek: recent.length,
        active: this.activeJobs().length,
      };
    },
    fmt(n) { return (n || 0).toLocaleString(); },
    filteredScrapes() {
      const q = this.scrapeQuery.trim().toLowerCase();
      return q ? this.scrapes.filter(s => (s.name + " " + s.domain + " " + s.columns.join(" ")).toLowerCase().includes(q)) : this.scrapes;
    },

    go(view) {
      this.view = view; this.menuOpen = false; this.error = null;
      if (view === "settings") this.loadSites();
      if (view === "scrapes") this.loadScrapes();
      if (view === "agent") this.loadAgentRuns();
      window.scrollTo({ top: 0 });
    },
    toast(msg, type = "success") {
      const id = Date.now() + Math.random();
      this.toasts.push({ id, msg, type });
      setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, 5500);
    },
    urls() {
      return [this.urlMain, ...this.urlsExtra.split("\n")].map(s => s.trim()).filter(Boolean);
    },
    fields() { return this.fieldsText.split(",").map(s => s.trim()).filter(Boolean); },
    hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } },
    isUrl(v) { return typeof v === "string" && /^https?:\/\//.test(v); },
    isImage(v) { return this.isUrl(v) && /\.(jpe?g|png|webp|gif|avif)(\?|$)/i.test(v); },
    pageTypeLabel(t) { return PAGE_TYPES[t] || ""; },
    eventLabel(e) { return EVENT_LABELS[e] || e; },
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
    needsYouJobs() { return this.jobs.filter(j => j.status === "needs_you"); },
    activeJobs() { return this.jobs.filter(j => ["queued", "running"].includes(j.status)); },
    jobTitle(j) { return j.scrape_name || this.hostOf(j.urls?.[0]) || ("Run #" + j.id); },

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

    // ---------------------------------------------------------------- wizard
    startWizard(template = null, url = "") {
      this.go("new");
      this.step = 1; this.template = template; this.editingScrapeId = null;
      this.analysis = null; this.recipeText = ""; this.blockedPage = null;
      this.urlMain = url || ""; this.urlsExtra = ""; this.showExtra = false;
      this.fieldsText = template ? template.columns.join(", ") : "";
      this.prompt = template?.prompt || ""; this.request = null; this.requestFor = ""; this.listMode = false;
      this.autoColumns = true; this.scrapeName = ""; this.personalChoice = {}; this.personalPurpose = "";
      this.maxPages = Math.min(3, this.status?.max_pages || 3); this.allPages = false;
      if (url) this.next();
    },

    pickTemplate(t) {
      const prev = this.template;
      this.template = prev?.id === t.id ? null : t;
      this.fieldsText = this.template ? this.template.columns.join(", ") : "";
      // Replace the description only if it is empty or still the previous example.
      if (!this.prompt.trim() || this.prompt === prev?.prompt) this.prompt = this.template?.prompt || "";
    },

    promptPlaceholder() {
      return "Example: I need the image, price and brand of each shoe. Only men's shoes under ₹3,000, cheapest first.";
    },
    needsUnderstanding() {
      return !this.listMode && !!this.prompt.trim() && (!this.request || this.requestFor !== this.prompt.trim());
    },
    // Step 1's main button: read the description first, then find the data.
    async next() {
      if (!this.urlMain.trim()) { this.error = { title: "Paste a link first", hint: "Copy the address of a page that lists items." }; return; }
      if (this.needsUnderstanding()) return this.understand();
      return this.analyse(false);
    },
    async understand() {
      this.error = null; this.busy = "understand";
      try {
        this.request = await api("POST", "/api/understand", { prompt: this.prompt.trim(), url: this.urls()[0] || "" });
        this.requestFor = this.prompt.trim();
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    typeLabel(t) { return TYPE_LABELS[t] || ""; },
    filterLabel(f) { return `${this.pretty(f.column)} ${OP_LABELS[f.op] || f.op} ${f.value}`; },
    removeReqColumn(name) {
      this.request.columns = this.request.columns.filter(c => c.name !== name);
      this.request.filters = this.request.filters.filter(f => f.column !== name);
      if (this.request.sort?.column === name) this.request.sort = { column: "", desc: false };
      if (!this.request.columns.length) this.request.auto = true;
    },
    async renameReqColumn(c) {
      const next = await this.promptDialog("Rename column", `New name for “${this.pretty(c.name)}”`, c.name, "Rename");
      const clean = (next || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
      if (!clean || clean === c.name || this.request.columns.some(x => x.name === clean)) return;
      this.request.filters.forEach(f => { if (f.column === c.name) f.column = clean; });
      if (this.request.sort?.column === c.name) this.request.sort.column = clean;
      c.name = clean; delete c.auto_added;
    },
    addReqColumn() {
      for (const raw of this.newReqColumn.split(",")) {
        const name = raw.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
        if (name && !this.request.columns.some(c => c.name === name)) {
          this.request.columns.push({ name, meaning: raw.trim(), type: "", required: true });
        }
      }
      this.newReqColumn = "";
    },
    removeFilter(i) { this.request.filters.splice(i, 1); },
    sortWords() {
      const s = this.request?.sort || {};
      const kind = (this.request?.columns || []).find(c => c.name === s.column)?.type;
      if (kind === "date") return s.desc ? "newest first" : "oldest first";
      if (["text", "url", "image"].includes(kind)) return s.desc ? "Z → A" : "A → Z";
      return s.desc ? "high → low" : "low → high";
    },
    cellText(v) {
      if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
      return v;
    },
    shortText(v) { const s = String(this.cellText(v) ?? ""); return s.length > 40 ? s.slice(0, 38) + "…" : s; },

    trySample() { this.startWizard(TEMPLATES[0], "https://books.toscrape.com/"); },

    async analyse(forceAi) {
      if (!this.urlMain.trim()) { this.error = { title: "Paste a link first", hint: "Copy the address of a page that lists items." }; return; }
      this.error = null; this.blockedPage = null; this.busy = forceAi ? "ai" : "analyse";
      try {
        const useRequest = !this.listMode && this.request && this.requestFor === this.prompt.trim();
        this.analysis = await api("POST", "/api/analyse",
          { url: this.urls()[0], fields: this.fields(), auto: this.autoColumns, force_ai: forceAi,
            request: useRequest ? this.request : null });
        this.afterAnalysis();
        this.step = 2;
      } catch (e) {
        if (e.detail?.starter_recipe) {
          this.showError(e);
          if (this.advanced) {
            this.analysis = { domain: e.detail.domain, source: "starter", rows: [], row_count: 0, report: {},
                              personal_fields: [], settings: {} };
            this.recipeText = JSON.stringify(e.detail.starter_recipe, null, 2);
            this.step = 2;
          }
        } else { this.handleError(e); }
      } finally { this.busy = ""; }
    },

    afterAnalysis() {
      this.recipeText = JSON.stringify(this.analysis.recipe, null, 2);
      this.applySettings(this.analysis.settings);
      this.personalChoice = Object.fromEntries((this.analysis.personal_fields || []).map(f => [f, false]));
      if (!this.scrapeName) {
        const kind = this.pageTypeLabel(this.analysis.page_type) || "Data";
        this.scrapeName = `${kind} – ${this.hostOf(this.analysis.url || this.urlMain)}`;
      }
      this.loadSites();
    },

    parseRecipe() {
      try { return JSON.parse(this.recipeText); }
      catch (e) { throw new Error("The column setup (JSON) has a mistake: " + e.message); }
    },

    async previewRecipe() {
      this.error = null; this.busy = "preview";
      try {
        this.analysis = await api("POST", "/api/preview", { url: this.urls()[0], recipe: this.parseRecipe() });
        this.afterAnalysis();
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },

    recipeColumns() {
      try { return (JSON.parse(this.recipeText).fields || []).map(f => f.name); } catch { return []; }
    },
    fillOf(col) { return this.analysis?.report?.[col] ?? null; },
    fillClass(col) {
      const v = this.fillOf(col);
      if (v === null) return "bg-slate-300";
      return v >= 0.8 ? "bg-emerald-500" : v >= 0.4 ? "bg-amber-400" : "bg-rose-400";
    },

    async removeColumn(name) {
      try {
        const r = this.parseRecipe();
        r.fields = (r.fields || []).filter(f => f.name !== name);
        if (!r.fields.length) { this.error = { title: "Keep at least one column" }; return; }
        if (r.detail) {
          r.detail.fields = r.detail.fields.filter(f => f.name !== name);
          if (!r.detail.fields.length) delete r.detail;
        }
        if (r.request) {
          r.request.columns = r.request.columns.filter(c => c.name !== name);
          r.request.filters = r.request.filters.filter(f => f.column !== name);
          if (r.request.sort?.column === name) r.request.sort = { column: "", desc: false };
          this.request = r.request;
        }
        if (r.column_types) delete r.column_types[name];
        this.recipeText = JSON.stringify(r, null, 2);
        await this.previewRecipe();
      } catch (e) { this.showError(e); }
    },

    async renameColumn(name) {
      const next = await this.promptDialog("Rename column", `New name for “${this.pretty(name)}”`, name, "Rename");
      if (!next || next.trim() === name) return;
      const clean = next.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
      if (!clean) return;
      try {
        const r = this.parseRecipe();
        if (r.fields.some(f => f.name === clean)) { this.error = { title: "That column name is already used" }; return; }
        r.fields.forEach(f => { if (f.name === name) f.name = clean; });
        (r.detail?.fields || []).forEach(f => { if (f.name === name) f.name = clean; });
        if (r.detail?.url_field === name) r.detail.url_field = clean;
        if (r.request) {
          r.request.columns.forEach(c => { if (c.name === name) c.name = clean; });
          r.request.filters.forEach(f => { if (f.column === name) f.column = clean; });
          if (r.request.sort?.column === name) r.request.sort.column = clean;
          this.request = r.request;
        }
        if (r.column_types && name in r.column_types) { r.column_types[clean] = r.column_types[name]; delete r.column_types[name]; }
        this.recipeText = JSON.stringify(r, null, 2);
        await this.previewRecipe();
      } catch (e) { this.showError(e); }
    },

    async addColumn() {
      const name = this.newColumn.trim();
      if (!name) return;
      const added = name.split(",").map(s => s.trim()).filter(Boolean);
      const wanted = [...new Set([...this.recipeColumns(), ...added])];
      let request = null;
      try { request = this.parseRecipe().request || null; } catch { request = null; }
      if (request) {
        request = JSON.parse(JSON.stringify(request));
        for (const raw of added) {
          const n = raw.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
          if (n && !request.columns.some(c => c.name === n)) request.columns.push({ name: n, meaning: raw, type: "", required: true });
        }
        request.auto = false;
      }
      this.error = null; this.busy = "add";
      try {
        this.analysis = await api("POST", "/api/analyse",
          { url: this.urls()[0], fields: wanted, auto: false, force_ai: true, request });
        if (request) { this.request = request; }
        this.afterAnalysis();
        this.newColumn = "";
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },

    personalFieldsChosen() { return Object.entries(this.personalChoice).filter(([, v]) => v).map(([k]) => k); },

    // ---- item pages (optional per scrape)
    detailOn() { try { return !!JSON.parse(this.recipeText).detail; } catch { return false; } },
    detailColumns() { try { return (JSON.parse(this.recipeText).detail?.fields || []).map(f => f.name); } catch { return []; } },
    detailSample() { return this.analysis?.detail_row || {}; },
    detailEstimate() {
      const n = this.analysis?.row_count || 0;
      const d = this.status?.delay || [2, 5];
      const per = (d[0] + d[1]) / 2 + 1;
      const mins = Math.max(1, Math.round(n * per / 60));
      return `About ${mins} extra minute${mins === 1 ? "" : "s"} for every page of ${n} items.`;
    },
    async toggleDetails() {
      let r;
      try { r = this.parseRecipe(); } catch (e) { return this.showError(e); }
      if (r.detail) {
        delete r.detail;
        this.recipeText = JSON.stringify(r, null, 2);
        return this.previewRecipe();
      }
      // Ask for the columns the list page doesn't have (from the description), plus anything useful.
      const have = new Set((r.fields || []).map(f => f.name));
      const wanted = (r.request?.columns || []).filter(c => !have.has(c.name));
      for (const name of this.analysis?.absent || []) if (!wanted.some(c => c.name === name)) wanted.push({ name, meaning: "", type: "" });
      this.error = null; this.busy = "details";
      try {
        this.analysis = await api("POST", "/api/details",
          { url: this.urls()[0], recipe: r, columns: wanted, auto: !r.request || r.request.auto || !wanted.length });
        this.afterAnalysis();
        this.toast("Item pages are on. Each run will open every item's page for these details.");
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    hasPaging() { return ["next_link", "url_param", "scroll", "click"].includes(this.analysis?.recipe?.pagination?.type); },
    pagesToCollect() { return this.allPages && this.hasPaging() ? 50 : this.maxPages; },

    paginationHint() {
      const t = this.analysis?.recipe?.pagination?.type;
      if (t === "next_link" || t === "url_param") return "The site has more pages; each one adds more rows.";
      if (t === "scroll") return "The site loads more items as you scroll; each 'page' is one scroll.";
      if (t === "click") return "The site has a 'Load more' button; each 'page' is one click.";
      return "Only this page seems to have items. Add more links in step 1 to collect more.";
    },

    async saveScrape(run) {
      this.error = null; this.busy = run ? "run" : "save";
      try {
        const chosen = this.personalFieldsChosen();
        if (chosen.length && this.personalPurpose.trim().length < 10) {
          throw new Error("Say why you need the personal data (at least 10 characters), or untick it.");
        }
        const recipe = this.parseRecipe();
        let jobId = null;
        if (this.editingScrapeId) {
          await api("PUT", `/api/scrapes/${this.editingScrapeId}`,
            { name: this.scrapeName, urls: this.urls(), recipe, max_pages: this.pagesToCollect() });
          if (run) jobId = (await api("POST", `/api/scrapes/${this.editingScrapeId}/run`,
            { personal_fields: chosen, personal_purpose: this.personalPurpose })).job_id;
        } else {
          const r = await api("POST", "/api/scrapes", {
            name: this.scrapeName || "My scrape", urls: this.urls(), recipe, max_pages: this.pagesToCollect(),
            page_type: this.analysis?.page_type || "", run,
            personal_fields: chosen, personal_purpose: this.personalPurpose,
          });
          jobId = r.job_id; this.editingScrapeId = r.scrape_id;
        }
        await Promise.all([this.loadScrapes(), this.loadJobs()]);
        if (run && jobId) {
          this.go("runs");
          await this.openJob(this.jobs.find(j => j.id === jobId));
          this.toast("Started. You can leave this page; it keeps running.");
        } else {
          this.go("scrapes");
          this.toast(`Saved “${this.scrapeName}”. Run it any time with Run again.`);
        }
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },

    // ---------------------------------------------------------------- saved scrapes
    async loadScrapes() {
      try { this.scrapes = await api("GET", "/api/scrapes"); } catch (e) { /* shown elsewhere */ }
    },

    async runScrape(s) {
      this.error = null;
      try {
        const { job_id } = await api("POST", `/api/scrapes/${s.id}/run`, {});
        await this.loadJobs();
        this.go("runs");
        await this.openJob(this.jobs.find(j => j.id === job_id));
        this.toast(`Running “${s.name}” again.`);
      } catch (e) { this.handleError(e); }
    },

    async editScrape(s) {
      try {
        const full = await api("GET", `/api/scrapes/${s.id}`);
        this.startWizard();
        this.editingScrapeId = full.id; this.scrapeName = full.name;
        this.allPages = full.max_pages >= 50; this.maxPages = Math.min(full.max_pages, Math.min(50, this.status?.max_pages || 10));
        const req = full.recipe.request;
        this.request = req || null; this.prompt = req?.prompt || ""; this.requestFor = this.prompt.trim();
        this.listMode = !req; this.fieldsText = req ? "" : (full.recipe.fields || []).map(f => f.name).join(", ");
        this.urlMain = full.urls[0] || ""; this.urlsExtra = full.urls.slice(1).join("\n");
        this.showExtra = full.urls.length > 1;
        this.recipeText = JSON.stringify(full.recipe, null, 2);
        this.step = 2;
        await this.previewRecipe();
      } catch (e) { this.showError(e); }
    },

    async duplicateScrape(s) {
      try { await api("POST", `/api/scrapes/${s.id}/duplicate`); await this.loadScrapes(); this.toast("Copy created."); }
      catch (e) { this.showError(e); }
    },

    async renameScrape(s) {
      const name = await this.promptDialog("Rename scrape", "Name", s.name, "Rename");
      if (!name || name === s.name) return;
      try { await api("PUT", `/api/scrapes/${s.id}`, { name }); await this.loadScrapes(); }
      catch (e) { this.showError(e); }
    },

    async deleteScrape(s) {
      if (!await this.confirmDialog(`Delete “${s.name}”?`, "The saved setup is removed. Its past results stay under Results until you delete them.")) return;
      try { await api("DELETE", `/api/scrapes/${s.id}`); await this.loadScrapes(); }
      catch (e) { this.showError(e); }
    },

    async repairScrape(job) {
      this.busy = "repair";
      try {
        await api("POST", `/api/scrapes/${job.scrape_id}/repair`);
        await this.loadScrapes();
        if (await this.confirmDialog("Columns re-detected", "The AI looked at the page again and updated this scrape's setup. Run it again now?", "Run again", false)) {
          await this.runAgainFromJob(job);
        }
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },

    async openLatestRun(s) {
      if (!s.last_run) return;
      await this.loadJobs();
      this.go("runs");
      await this.openJob(this.jobs.find(j => j.id === s.last_run.id));
    },

    // ---------------------------------------------------------------- runs / results
    async loadJobs() {
      const wasActive = this.activeJobs().map(j => j.id);
      try { this.jobs = await api("GET", "/api/jobs?limit=100"); } catch (e) { /* server may be restarting */ }
      const active = this.activeJobs().length > 0;
      clearTimeout(this.pollTimer);
      this.pollTimer = setTimeout(() => this.loadJobs(), active ? 2000 : 8000);
      if (wasActive.some(id => !this.activeJobs().find(j => j.id === id))) this.loadScrapes();
      if (this.selectedJob) {
        const fresh = this.jobs.find(j => j.id === this.selectedJob.id);
        if (fresh && (fresh.rows !== this.selectedJob.rows || fresh.status !== this.selectedJob.status)) {
          this.selectedJob = fresh;
          await this.loadRecords();
        } else if (fresh) { this.selectedJob = fresh; }
      }
    },

    async openJob(j) {
      if (!j) return;
      this.selectedJob = j; this.search = ""; this.sortKey = ""; this.showPersonal = false; this.page = 1;
      this.resultTab = "data"; this.compare = null; this.compareError = "";
      this.hiddenCols = store("hidden:" + (j.scrape_id || j.id)) || [];
      await this.loadRecords();
    },

    async loadRecords() {
      try {
        this.records = await api("GET", `/api/jobs/${this.selectedJob.id}/records?show_personal=${this.showPersonal}`);
      } catch (e) { this.showError(e); }
    },

    allColumns() { return this.columns(this.records); },
    visibleColumns() { return this.allColumns().filter(c => !this.hiddenCols.includes(c)); },
    toggleCol(c) {
      this.hiddenCols = this.hiddenCols.includes(c) ? this.hiddenCols.filter(x => x !== c) : [...this.hiddenCols, c];
      store("hidden:" + (this.selectedJob.scrape_id || this.selectedJob.id), this.hiddenCols);
    },
    exportUrl(fmt) {
      const cols = this.visibleColumns().join(",");
      const name = encodeURIComponent(this.jobTitle(this.selectedJob || {}));
      return `/api/jobs/${this.selectedJob?.id}/export?fmt=${fmt}&show_personal=${this.showPersonal}&columns=${encodeURIComponent(cols)}&name=${name}`;
    },

    async stopJob(j) {
      await api("POST", `/api/jobs/${j.id}/stop`).catch(e => this.showError(e));
      this.loadJobs();
    },
    async resumeJob(j) {
      try { await api("POST", `/api/jobs/${j.id}/resume`); this.loadJobs(); } catch (e) { this.showError(e); }
    },
    async toggleKeep(j) {
      try { await api("POST", `/api/jobs/${j.id}/keep`, { keep: !j.keep }); j.keep = !j.keep; } catch (e) { this.showError(e); }
    },
    async deleteJob(j) {
      if (!await this.confirmDialog("Delete this run?", `This removes the run and its ${this.fmt(j.rows)} rows. This can't be undone.`)) return;
      try {
        await api("DELETE", `/api/jobs/${j.id}`);
        if (this.selectedJob?.id === j.id) { this.selectedJob = null; this.records = []; }
        await Promise.all([this.loadJobs(), this.loadScrapes()]);
      } catch (e) { this.showError(e); }
    },
    async runAgainFromJob(j) {
      const s = this.scrapes.find(x => x.id === j.scrape_id);
      if (s) return this.runScrape(s);
    },

    resultTabs() {
      const q = this.selectedJob?.quality || {};
      const warn = (q.warnings || []).length;
      const tabs = [{ id: "data", label: "Data", badge: this.fmt(this.selectedJob?.rows) },
                    { id: "quality", label: "Quality", badge: warn ? String(warn) : "", warn: !!warn }];
      if (this.selectedJob?.scrape_id) tabs.push({ id: "changes", label: "Changes" });
      return tabs;
    },
    openResultTab(id) {
      this.resultTab = id;
      if (id === "changes" && !this.compare) this.loadCompare();
    },
    qualityTiles() {
      const q = this.selectedJob?.quality || {};
      const tiles = [
        { label: "Rows saved", value: this.fmt(q.rows) },
        { label: "Pages read", value: this.fmt(q.pages_ok), note: q.pages_failed?.length ? `${q.pages_failed.length} failed` : "", warn: !!q.pages_failed?.length },
        { label: "Duplicates removed", value: this.fmt(q.duplicates) },
        { label: "Didn't match request", value: this.fmt(q.excluded), note: q.trimmed ? `${this.fmt(q.trimmed)} over the limit` : "" },
      ];
      if (q.coverage !== null && q.coverage !== undefined) {
        tiles.push({ label: "Coverage", value: Math.round(q.coverage * 100) + "%", note: `of about ${this.fmt(q.expected_total)} on the site`, warn: q.coverage < 0.9 });
      }
      if (q.route_steps) {
        tiles.push({ label: "Steps replayed", value: this.fmt(q.route_steps), note: "recorded by the agent · no AI" });
      }
      if (q.details?.done || q.details?.failed) {
        tiles.push({ label: "Item pages read", value: this.fmt(q.details.done), note: q.details.failed ? `${q.details.failed} failed` : "", warn: !!q.details.failed });
      }
      return tiles;
    },
    async loadCompare() {
      this.busy = "compare"; this.compareError = ""; this.compare = null;
      try {
        this.compare = await api("GET", `/api/jobs/${this.selectedJob.id}/compare?show_personal=${this.showPersonal}`);
        this.compareView = this.compare.counts.added ? "added" : this.compare.counts.changed ? "changed" : "removed";
      } catch (e) { this.compareError = e.status === 404 ? "This is the first finished run of this scrape, so there is nothing to compare with yet." : this.explain(e).hint; }
      finally { this.busy = ""; }
    },
    compareRows() { return this.compare?.[this.compareView] || []; },
    rowOf(item) { return item.row || item; },
    compareColumns() { return this.columns(this.compareRows().slice(0, 50).map(i => this.rowOf(i))).filter(c => !/_(currency|unit)$/.test(c)).slice(0, 8); },

    sortBy(c) { if (this.sortKey === c) this.sortAsc = !this.sortAsc; else { this.sortKey = c; this.sortAsc = true; } },
    filtered() {
      const q = this.search.toLowerCase();
      let rows = q ? this.records.filter(r => Object.values(r).some(v => String(v).toLowerCase().includes(q)))
                   : this.records.slice();
      if (this.sortKey) {
        const k = this.sortKey, dir = this.sortAsc ? 1 : -1;
        const num = v => parseFloat(String(v).replace(/[^0-9.\-]/g, ""));
        rows.sort((a, b) => {
          const x = num(a[k]), y = num(b[k]);
          if (!isNaN(x) && !isNaN(y)) return (x - y) * dir;
          return String(a[k] ?? "").localeCompare(String(b[k] ?? "")) * dir;
        });
      }
      return rows;
    },
    totalPages() { return Math.max(1, Math.ceil(this.filtered().length / this.pageSize)); },
    pagedRows() {
      const p = Math.min(this.page, this.totalPages());
      return this.filtered().slice((p - 1) * this.pageSize, p * this.pageSize);
    },
    rangeText() {
      const n = this.filtered().length;
      if (!n) return "0 rows";
      const p = Math.min(this.page, this.totalPages());
      return `${this.fmt((p - 1) * this.pageSize + 1)}–${this.fmt(Math.min(p * this.pageSize, n))} of ${this.fmt(n)}`;
    },
    progressPct(j) {
      if (!j) return 0;
      if (j.status === "done") return 100;
      return j.pages_total ? Math.max(5, Math.round(100 * j.pages_done / j.pages_total)) : 5;
    },

    // ---------------------------------------------------------------- site settings (advanced)
    settingsSummary() {
      const s = this.settings, d = this.status?.delay || [];
      const lo = s.min_delay ?? d[0], hi = s.max_delay ?? d[1];
      const bits = [`wait ${lo}–${hi}s between pages`, s.render === "always" ? "loads like a browser" : s.render === "never" ? "simple loading" : "automatic loading"];
      if (s.daily_budget) bits.push(`limit ${s.daily_budget} pages/day`);
      return bits.join(" · ");
    },
    applySettings(s) {
      s = s || {};
      this.settings = {
        min_delay: s.min_delay ?? null, max_delay: s.max_delay ?? null, render: s.render || "auto",
        daily_budget: s.daily_budget ?? null, honest_ua: !!s.honest_ua,
        ai_allowed: s.ai_allowed !== false, use_session: s.use_session !== false, blocked: !!s.blocked,
      };
    },
    async saveSettings() {
      try {
        const s = this.settings;
        const body = { render: s.render, honest_ua: s.honest_ua, ai_allowed: s.ai_allowed,
                       use_session: s.use_session, blocked: s.blocked };
        for (const k of ["min_delay", "max_delay", "daily_budget"]) if (s[k] !== null && s[k] !== "") body[k] = s[k];
        this.applySettings(await api("PUT", `/api/sites/${this.analysis.domain}/settings`, body));
        this.toast("Site settings saved.");
        this.loadSites();
      } catch (e) { this.showError(e); }
    },
    async saveRecipeForSite() {
      try {
        await api("PUT", `/api/sites/${this.analysis.domain}/recipe`, { recipe: this.parseRecipe() });
        this.toast("Column setup saved for " + this.analysis.domain + ".");
      } catch (e) { this.showError(e); }
    },

    // ---------------------------------------------------------------- sites & logins
    async loadSites() { try { this.sites = await api("GET", "/api/sites"); } catch (e) { /* not fatal */ } },
    async forgetSession(site) {
      if (!await this.confirmDialog("Remove saved login?", `You'll need to log in to ${site.session.domain} again before scraping pages that need it.`, "Remove")) return;
      try { await api("DELETE", `/api/sessions/${site.session.domain}`); this.loadSites(); } catch (e) { this.showError(e); }
    },
    async clearCooldown(site) {
      try { await api("DELETE", `/api/sites/${site.domain}/cooldown`); this.loadSites(); } catch (e) { this.showError(e); }
    },
    async toggleBlocked(site) {
      try { await api("PUT", `/api/sites/${site.domain}/settings`, { blocked: !site.settings?.blocked }); this.loadSites(); }
      catch (e) { this.showError(e); }
    },
    async deleteSiteData(site) {
      if (!await this.confirmDialog(`Delete all data for ${site.domain}?`, "Runs, results, cached pages and the saved login for this site are removed. This can't be undone.")) return;
      try {
        const r = await api("DELETE", `/api/sites/${site.domain}/data`);
        this.toast(`Deleted ${r.jobs} run(s) and ${r.cache_pages} cached page(s).`);
        if (this.selectedJob && !this.jobs.find(j => j.id === this.selectedJob.id)) this.selectedJob = null;
        await Promise.all([this.loadSites(), this.loadJobs(), this.loadScrapes()]);
      } catch (e) { this.showError(e); }
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
          this.toast(`Thanks. ${w.domain} is open again; try Find the data once more.`);
          this.blockedPage = null;
        } else {
          await api("POST", `/api/jobs/${w.jobId}/solve/finish`, { account: w.account });
          this.toast("The scrape continues.");
        }
        this.win = null;
        await Promise.all([this.loadSites(), this.loadJobs()]);
      } catch (e) { this.showError(e); }
      finally { this.busy = ""; }
    },
    async cancelWindow() {
      const w = this.win;
      this.win = null; clearTimeout(this.winTimer);
      if (w?.mode === "login") await api("POST", `/api/sites/${w.domain}/login/cancel`).catch(() => {});
    },

    // ---------------------------------------------------------------- agent mode
    agentEnabled() { return !!this.status?.agent?.enabled && this.agentShown; },
    agentProblem() {
      const a = this.status?.agent;
      if (!a) return "";
      if (!a.installed) return "The agent's environment isn't installed. In the project folder run:  python -m venv .venv-agent  then  .venv-agent\\Scripts\\pip install -r requirements-agent.txt";
      if (!a.key_set) return `It needs an AI key for ${a.provider} in .env.`;
      if (!a.browser) return "It needs Microsoft Edge or Google Chrome installed.";
      return "";
    },
    agentBusyCount() { return this.agentRuns.filter(r => ["queued", "navigating", "collecting"].includes(r.status)).length; },
    agentActive() { return ["queued", "navigating", "collecting"].includes(this.agentRun?.status); },
    agentStatusLabel(s) {
      return { draft: "Plan ready", queued: "Starting", navigating: "Finding the results", collecting: "Collecting",
               done: "Finished", stopped: "Stopped", needs_you: "Needs you", limit: "Hit a limit", error: "Failed" }[s] || s;
    },
    agentChip(s) {
      return { done: "chip-green", navigating: "chip-blue", collecting: "chip-blue", queued: "chip-amber", draft: "chip-gray",
               needs_you: "chip-amber", limit: "chip-amber", stopped: "chip-gray", error: "chip-red" }[s] || "chip-gray";
    },
    agentSteps() { return this.agentRun?.steps_list || []; },
    agentTiles() {
      const r = this.agentRun || {};
      const secs = r.elapsed_s || 0;
      return [
        { label: "Agent steps", value: `${r.steps || 0} / ${r.limits?.steps || "—"}` },
        { label: "AI calls", value: `${r.ai_calls || 0} / ${r.limits?.ai_calls || "—"}` },
        { label: "Time", value: secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s` },
        { label: "Rows", value: this.fmt(r.job?.rows || 0) },
      ];
    },
    async loadAgentRuns() {
      if (!this.status?.agent?.enabled) return;
      try { this.agentRuns = await api("GET", "/api/agent/runs"); } catch { /* not fatal */ }
    },
    newAgentRun() { clearTimeout(this.agentTimer); this.agentRun = null; this.error = null; },
    async planAgent() {
      if (!this.agentGoal.trim() || !this.agentSite.trim()) {
        this.error = { title: "Say what to collect and on which site", hint: "Fill in both boxes, or click an example." }; return;
      }
      this.error = null; this.busy = "agent-plan";
      try {
        this.agentRun = await api("POST", "/api/agent/plan", { goal: this.agentGoal.trim(), site: this.agentSite.trim() });
        this.agentLimits = { ...(this.status?.agent?.limits || this.agentLimits) };
        this.loadAgentRuns();
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    agentRemoveColumn(name) {
      const req = this.agentRun.plan.request;
      req.columns = req.columns.filter(c => c.name !== name);
      req.filters = req.filters.filter(f => f.column !== name);
      if (req.sort.column === name) req.sort = { column: "", desc: false };
    },
    agentAddColumn() {
      const req = this.agentRun.plan.request;
      for (const raw of this.agentNewCol.split(",")) {
        const name = raw.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
        if (name && !req.columns.some(c => c.name === name)) req.columns.push({ name, meaning: raw.trim(), type: "", required: true });
      }
      this.agentNewCol = "";
    },
    async discardAgentPlan() {
      const r = this.agentRun;
      this.agentRun = null;
      if (r?.status === "draft") { await api("DELETE", `/api/agent/runs/${r.id}`).catch(() => {}); this.loadAgentRuns(); }
    },
    async startAgent() {
      const r = this.agentRun;
      this.error = null; this.busy = "agent-start";
      try {
        r.plan.navigation = r.plan.navigation.map(s => s.trim()).filter(Boolean);
        await api("PUT", `/api/agent/runs/${r.id}/plan`, { plan: r.plan });
        this.agentRun = await api("POST", `/api/agent/runs/${r.id}/start`, this.agentLimits);
        this.loadAgentRuns();
        this.pollAgent();
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    async openAgentRun(id) {
      clearTimeout(this.agentTimer);
      try {
        this.agentRun = await api("GET", `/api/agent/runs/${id}`);
        if (this.agentActive()) this.pollAgent();
      } catch (e) { this.showError(e); }
    },
    async pollAgent() {
      clearTimeout(this.agentTimer);
      const r = this.agentRun;
      if (!r) return;
      try {
        const last = r.steps_list?.length ? r.steps_list[r.steps_list.length - 1].n : 0;
        const fresh = await api("GET", `/api/agent/runs/${r.id}?after=${last}`);
        if (this.agentRun?.id !== r.id) return;
        fresh.steps_list = [...(r.steps_list || []), ...fresh.steps_list];
        this.agentRun = fresh;
        const was = this.agentRuns.find(x => x.id === fresh.id);
        if (was && was.status !== fresh.status) this.loadAgentRuns();
      } catch { /* server busy; try again */ }
      if (this.agentActive()) this.agentTimer = setTimeout(() => this.pollAgent(), 2000);
      else { this.loadAgentRuns(); this.loadJobs(); this.loadScrapes(); }
    },
    async stopAgent() {
      try { this.agentRun = { ...this.agentRun, ...(await api("POST", `/api/agent/runs/${this.agentRun.id}/stop`)), steps_list: this.agentRun.steps_list }; }
      catch (e) { this.showError(e); }
    },
    async retryAgent() {
      const r = this.agentRun;
      this.agentGoal = r.goal; this.agentSite = r.start_url;
      this.agentRun = null;
      await this.planAgent();
    },
    // A human check stopped the agent: solve it yourself in a visible window (same flow as Guided mode);
    // the saved browser session is given to the agent's next run.
    async solveForAgent() {
      const r = this.agentRun;
      this.blockedPage = { domain: r.site, url: r.final_url || r.start_url };
      await this.startSiteSolve();
    },
    async runAgentScrapeAgain() {
      await this.loadScrapes();
      const s = this.scrapes.find(x => x.id === this.agentRun?.scrape_id);
      if (s) await this.runScrape(s);
    },
    async rerecordScrape(scrapeId) {
      if (!scrapeId) return;
      this.error = null; this.busy = "agent-start";
      try {
        const run = await api("POST", `/api/agent/rerecord/${scrapeId}`, this.status?.agent?.limits || this.agentLimits);
        this.go("agent");
        this.agentRun = run;
        this.loadAgentRuns();
        this.pollAgent();
        this.toast("The agent is finding the way again. The saved scrape is updated when it's done.");
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    async openAgentResults() {
      const id = this.agentRun?.job_id;
      if (!id) return;
      await this.loadJobs();
      this.go("runs");
      await this.openJob(this.jobs.find(j => j.id === id));
    },

    // ---------------------------------------------------------------- activity
    async loadActivity() { try { this.activity = await api("GET", "/api/audit?limit=150"); } catch (e) { this.showError(e); } },
    eventDetail(a) {
      const parts = [];
      if (a.job_id) parts.push("run #" + a.job_id);
      if (a.url) parts.push(a.url); else if (a.domain) parts.push(a.domain);
      if (a.status && a.status !== 200) parts.push("HTTP " + a.status);
      if (a.rendered) parts.push("browser");
      if (a.fmt) parts.push(a.fmt.toUpperCase() + ", " + a.rows + " rows");
      if (a.reason) parts.push(a.reason);
      return parts.join(" · ");
    },
  };
}
