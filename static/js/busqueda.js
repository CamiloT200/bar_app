// Búsqueda compartida: compara textos ignorando mayúsculas y tildes.
(function () {
  "use strict";

  // "Águila" -> "aguila"
  function normalizar(texto) {
    return (texto || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
  }

  window.Busqueda = { normalizar: normalizar };
})();
