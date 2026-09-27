// JavaScript mínimo, sin librerías. Todo lo que modifica datos es un
// formulario POST normal; esto solo agrega comodidad.
(function () {
  "use strict";

  // Igual al filtro de plantilla `pesos`: 45000 -> "$ 45.000".
  window.formatoPesos = function (valor) {
    var signo = valor < 0 ? "-" : "";
    return signo + "$ " + Math.abs(valor).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  };

  // Evitar doble toque: un formulario solo se envía una vez. Los botones se
  // deshabilitan después del envío (setTimeout) para no perder el
  // name/value del botón que se tocó, que ya quedó en los datos del envío.
  document.addEventListener("submit", function (evento) {
    var form = evento.target;
    if (form.dataset.enviando) {
      evento.preventDefault();
      return;
    }
    form.dataset.enviando = "1";
    form.classList.add("enviando");
    setTimeout(function () {
      form.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    }, 0);
  });

  // Si el navegador restaura la página desde caché (botón Atrás), reactivar.
  window.addEventListener("pageshow", function (evento) {
    if (!evento.persisted) return;
    document.querySelectorAll("form.enviando").forEach(function (form) {
      delete form.dataset.enviando;
      form.classList.remove("enviando");
      form.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
    });
  });
})();
