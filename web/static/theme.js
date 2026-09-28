// Se carga en <head>, antes de pintar, para no parpadear al cambiar de tema.
// Orden: lo que el usuario eligió (botón o Ajustes → Apariencia) > lo que
// dice el navegador > oscuro (preferido cuando el navegador no indica nada).
(function () {
  var saved = null;
  try { saved = localStorage.getItem("mivpn-theme"); } catch (e) {}
  var light = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  document.documentElement.dataset.theme = saved || (light ? "light" : "dark");
})();
