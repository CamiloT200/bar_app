from django import forms

from inventario.models import Producto

from .models import Cuenta, MetodoPago

# Cantidades rápidas del selector: (cantidad, etiqueta opcional), más "Otra".
# Media y Canasta son solo cantidades, no productos ni precios especiales.
CANTIDADES_RAPIDAS = [(n, "") for n in range(1, 11)] + [(15, "Media"), (30, "Canasta")]
CANTIDAD_OTRA = "otra"
CANTIDAD_MAXIMA = 999


class AbrirCuentaForm(forms.Form):
    identificador = forms.CharField(max_length=Cuenta._meta.get_field("identificador").max_length)


class AgregarItemForm(forms.Form):
    """`cantidad` es un botón rápido ("1".."30") u "otra"; con "otra" vale `cantidad_otra`."""

    producto = forms.ModelChoiceField(queryset=Producto.objects.filter(activo=True))
    cantidad = forms.CharField()
    cantidad_otra = forms.IntegerField(required=False, min_value=1, max_value=CANTIDAD_MAXIMA)
    forzar = forms.BooleanField(required=False)
    nota = forms.CharField(required=False, max_length=255)

    def clean(self):
        datos = super().clean()
        elegida = datos.get("cantidad")
        if elegida == CANTIDAD_OTRA:
            if datos.get("cantidad_otra") is None and "cantidad_otra" not in self.errors:
                self.add_error("cantidad_otra", "Escribe la cantidad.")
            datos["cantidad"] = datos.get("cantidad_otra")
        elif elegida is not None:
            try:
                cantidad = int(elegida)
            except ValueError:
                cantidad = 0
            if not 1 <= cantidad <= CANTIDAD_MAXIMA:
                self.add_error("cantidad", f"La cantidad debe estar entre 1 y {CANTIDAD_MAXIMA}.")
            datos["cantidad"] = cantidad
        return datos


class CobrarForm(forms.Form):
    metodo_pago = forms.ChoiceField(choices=MetodoPago.choices, required=False)
