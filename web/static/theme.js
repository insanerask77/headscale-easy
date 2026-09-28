// Loaded in <head>, before painting, so the theme never flickers.
// Order: the user's choice > the browser's preference > dark (the default
// when the browser says nothing).
(function () {
  var saved = null;
  try { saved = localStorage.getItem("hse-theme"); } catch (e) {}
  var light = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  document.documentElement.dataset.theme = saved || (light ? "light" : "dark");
})();
