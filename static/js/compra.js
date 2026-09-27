// Registrar compra: agregar/vaciar líneas del formset, autocompletar el
// último costo y mostrar subtotales. El total guardado lo calcula el servidor.
(function () {
  "use strict";

  var contenedor = document.getElementById("lineas");
  var plantilla = document.getElementById("plantilla-linea");
  var totalFormas = document.getElementById("id_lineas-TOTAL_FORMS");
  var totalCompra = document.getElementById("total-compra");
  var pesos = window.formatoPesos;

  function entero(input) {
    var n = parseInt(input.value, 10);
    return isNaN(n) ? null : n;
  }

  function recalcular() {
    var total = 0;
    contenedor.querySelectorAll(".linea-compra").forEach(function (linea) {
      var cantidad = entero(linea.querySelector(".campo-cantidad"));
      var costo = entero(linea.querySelector(".campo-costo"));
      var celda = linea.querySelector(".subtotal-linea");
      if (cantidad !== null && costo !== null) {
        celda.textContent = pesos(cantidad * costo);
        total += cantidad * costo;
      } else {
        celda.textContent = "";
      }
    });
    totalCompra.textContent = pesos(total);
  }

  // Al elegir producto se propone su último costo, sin pisar uno ya escrito.
  contenedor.addEventListener("change", function (evento) {
    if (!evento.target.classList.contains("campo-producto")) return;
    var opcion = evento.target.selectedOptions[0];
    var costo = evento.target.closest(".linea-compra").querySelector(".campo-costo");
    if (opcion && opcion.dataset.costo && !costo.value) costo.value = opcion.dataset.costo;
    recalcular();
  });
  contenedor.addEventListener("input", recalcular);

  // Vaciar una línea: queda sin cambios y el formset la ignora.
  contenedor.addEventListener("click", function (evento) {
    var boton = evento.target.closest(".quitar-linea");
    if (!boton) return;
    var linea = boton.closest(".linea-compra");
    linea.querySelectorAll("input, select").forEach(function (campo) { campo.value = ""; });
    recalcular();
  });

  document.getElementById("agregar-linea").addEventListener("click", function () {
    var indice = parseInt(totalFormas.value, 10);
    var html = plantilla.innerHTML.replace(/__prefix__/g, indice);
    contenedor.insertAdjacentHTML("beforeend", html);
    totalFormas.value = indice + 1;
    var nueva = contenedor.lastElementChild;
    nueva.querySelector(".campo-producto").focus();
  });

  recalcular();
})();
