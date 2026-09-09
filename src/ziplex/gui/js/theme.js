// Light/dark theme -- purely client-side (localStorage), same shape as
// i18n.js's own display-language switcher: no server round-trip, no
// relation to a packed project's own content. Defaults to dark -- this
// app's original, only look until this existed -- when nothing's been
// chosen yet. Deliberately never follows prefers-color-scheme: the whole
// point of an explicit in-app switch is that it doesn't silently change
// out from under a user's own OS-wide setting: no light theme was even
// nominally supported until this existed either.
//
// Applied via a `data-theme` attribute on <html> -- style.css's
// `:root[data-theme="light"]` block overrides the dark values every other
// rule already reads through CSS custom properties, so nothing here needs
// to know which specific rules exist. index.html's own inline <script> in
// <head> (not this module, an ES module and so deferred past first paint)
// sets that same attribute from the same localStorage key immediately on
// load, before first paint, to avoid a flash of the wrong theme -- keep
// that inline copy and THEME_KEY below in sync if this ever changes.
const THEME_KEY = "ziplex.theme";

export function getTheme() {
  return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
}

export function setTheme(theme) {
  const value = theme === "light" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, value);
  document.documentElement.setAttribute("data-theme", value);
}
