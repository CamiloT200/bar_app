from django import forms

from inventario.models import Producto

from .models import Cuenta, MetodoPago

# Cantidades rápidas del selector: (cantidad, etiqueta opcional).
# Media y Canasta son solo cantidades, no productos ni precios especiales.
CANTIDADES_RAPIDAS = [(1, ""), (2, ""), (3, ""), (6, ""), (12, ""), (15, "Media"), (30, "Canasta")]


class AbrirCuentaForm(forms.Form):
    identificador = forms.CharField(max_length=Cuenta._meta.get_field("identificador").max_length)


class AgregarItemForm(forms.Form):
    producto = forms.ModelChoiceField(queryset=Producto.objects.filter(activo=True))
    cantidad = forms.IntegerField(min_value=1, max_value=999)
    forzar = forms.BooleanField(required=False)
    nota = forms.CharField(required=False, max_length=255)


class CobrarForm(forms.Form):
    metodo_pago = forms.ChoiceField(choices=MetodoPago.choices, required=False)
