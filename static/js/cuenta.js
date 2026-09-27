// Pantalla de cuenta: búsqueda de productos sin servidor, filtro por
// categoría, Enter para agregar y ventana de confirmación para quitar.
(function () {
  "use strict";

  var normalizar = window.Busqueda.normalizar;  // static/js/busqueda.js

  var buscador = document.getElementById("buscar-producto");
  var productos = Array.prototype.slice.call(document.querySelectorAll(".btn-producto"));
  var pestanas = Array.prototype.slice.call(document.querySelectorAll(".pestanas-categoria [data-categoria]"));
  var sinResultados = document.getElementById("sin-resultados");
  var categoriaActiva = "";

  productos.forEach(function (boton) {
    boton.dataset.normalizado = normalizar(boton.dataset.nombre);
  });

  function visibles() {
    return productos.filter(function (boton) { return !boton.classList.contains("d-none"); });
  }

  function filtrar() {
    var texto = normalizar(buscador ? buscador.value : "");
    productos.forEach(function (boton) {
      var coincide = boton.dataset.normalizado.indexOf(texto) !== -1 &&
        (!categoriaActiva || boton.dataset.categoria === categoriaActiva);
      boton.classList.toggle("d-none", !coincide);
    });
    if (sinResultados) sinResultados.classList.toggle("d-none", visibles().length > 0);
  }

  if (buscador) {
    buscador.addEventListener("input", filtrar);
    buscador.addEventListener("keydown", function (evento) {
      if (evento.key === "Enter") {
        evento.preventDefault();
        var quedan = visibles();
        if (quedan.length === 1) quedan[0].click();
      } else if (evento.key === "Escape") {
        buscador.value = "";
        filtrar();
      }
    });
  }

  pestanas.forEach(function (pestana) {
    pestana.addEventListener("click", function () {
      categoriaActiva = pestana.dataset.categoria;
      pestanas.forEach(function (p) { p.classList.toggle("active", p === pestana); });
      filtrar();
    });
  });

  // La ventana de quitar recibe la URL y el texto del botón que la abrió.
  var modal = document.getElementById("modal-quitar");
  if (modal) {
    modal.addEventListener("show.bs.modal", function (evento) {
      var boton = evento.relatedTarget;
      document.getElementById("form-quitar").action = boton.dataset.url;
      document.getElementById("texto-quitar").textContent = boton.dataset.texto;
    });
  }
})();
