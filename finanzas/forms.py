from django import forms

from inventario.forms import EstiloBootstrapMixin

from ventas.models import MetodoPago

from .models import CategoriaGasto


class AbrirCajaForm(forms.Form):
    base_inicial = forms.IntegerField(min_value=0)


class CerrarCajaForm(forms.Form):
    # Lo llena el JavaScript sumando las denominaciones: es lo único que se guarda.
    efectivo_contado = forms.IntegerField(
        min_value=0, error_messages={"required": "Cuenta el efectivo antes de cerrar."}
    )
    observaciones = forms.CharField(required=False, widget=forms.Textarea)
    # Cifras que se mostraron al abrir la pantalla, para avisar si cambiaron.
    cifras_previas = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Lo que muestra la app o el datáfono de cada medio distinto al efectivo.
        for medio, etiqueta in MetodoPago.choices:
            if medio != MetodoPago.EFECTIVO:
                self.fields[f"contado_{medio}"] = forms.IntegerField(
                    min_value=0,
                    error_messages={"required": f"Escribe lo que dice {etiqueta} (0 si no hubo)."},
                )

    def contados_medios(self):
        return {
            nombre.removeprefix("contado_"): valor
            for nombre, valor in self.cleaned_data.items()
            if nombre.startswith("contado_")
        }


class GastoForm(EstiloBootstrapMixin, forms.Form):
    categoria = forms.ChoiceField(choices=CategoriaGasto.choices, widget=forms.RadioSelect)
    descripcion = forms.CharField(label="Descripción", max_length=255)
    monto = forms.IntegerField(
        label="Monto ($)", min_value=1, widget=forms.NumberInput(attrs={"inputmode": "numeric"})
    )
    pagado_con_caja = forms.BooleanField(
        label="Pagado con dinero de la caja", required=False, initial=True
    )
    # Vacío = ahora, igual que en compras.
    fecha = forms.DateTimeField(
        label="Fecha (vacío = ahora)",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
