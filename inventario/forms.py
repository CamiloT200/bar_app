from django import forms
from django.forms import formset_factory

from .models import Categoria, Producto, Proveedor, TipoMovimiento
from .texto import normalizar_nombre


class EstiloBootstrapMixin:
    """Pone las clases de Bootstrap (tamaño táctil) a todos los campos."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            widget = campo.widget
            if isinstance(widget, forms.CheckboxInput):
                clase = "form-check-input"
            elif isinstance(widget, forms.Select):
                clase = "form-select form-control-tactil"
            else:
                clase = "form-control form-control-tactil"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {clase}".strip()


class NombreUnicoMixin:
    """Nombre único ignorando mayúsculas y tildes ("Aguila" == "águila").

    SQLite no compara ignorando tildes, así que se valida aquí.
    """

    def clean_nombre(self):
        nombre = " ".join(self.cleaned_data["nombre"].split())
        clave = normalizar_nombre(nombre)
        otros = self._meta.model.objects.exclude(pk=self.instance.pk)
        for existente in otros.only("nombre"):
            if normalizar_nombre(existente.nombre) == clave:
                raise forms.ValidationError(f"Ya existe «{existente.nombre}».")
        return nombre


class ProductoForm(NombreUnicoMixin, EstiloBootstrapMixin, forms.ModelForm):
    class Meta:
        model = Producto
        fields = ["nombre", "categoria", "precio_venta", "stock_minimo", "activo"]
        labels = {
            "precio_venta": "Precio de venta ($)",
            "stock_minimo": "Stock mínimo (alerta)",
            "activo": "Activo (aparece en ventas)",
        }
        widgets = {
            "precio_venta": forms.NumberInput(attrs={"inputmode": "numeric", "step": 100}),
            "stock_minimo": forms.NumberInput(attrs={"inputmode": "numeric"}),
        }


class ProductoNuevoForm(ProductoForm):
    stock_inicial = forms.IntegerField(
        label="Inventario inicial (lo que hay hoy)",
        min_value=0,
        required=False,
        initial=0,
        widget=forms.NumberInput(attrs={"inputmode": "numeric"}),
    )


class CategoriaForm(NombreUnicoMixin, EstiloBootstrapMixin, forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["nombre"]


class CompraForm(EstiloBootstrapMixin, forms.Form):
    proveedor = forms.ModelChoiceField(
        queryset=Proveedor.objects.filter(activo=True),
        required=False,
        empty_label="— Sin proveedor —",
    )
    # Vacío = ahora. Precargarla con la hora actual (al minuto) podría caer
    # antes de un cierre hecho en ese mismo minuto.
    fecha = forms.DateTimeField(
        label="Fecha (vacío = ahora)",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    numero_factura = forms.CharField(label="N.º de factura", max_length=50, required=False)
    pagada_con_caja = forms.BooleanField(
        label="Pagada con dinero de la caja", required=False, initial=True
    )


class LineaCompraForm(forms.Form):
    """Las filas vacías del formset se ignoran (no cambiaron)."""

    producto = forms.ModelChoiceField(queryset=Producto.objects.filter(activo=True))
    cantidad = forms.IntegerField(min_value=1)
    costo_unitario = forms.IntegerField(min_value=0)


LineaCompraFormSet = formset_factory(LineaCompraForm, extra=5)


class MovimientoForm(forms.Form):
    """Una sola casilla numérica; su significado depende del tipo:
    merma = unidades que salieron, ajuste = conteo físico, inicial = unidades.
    """

    TIPOS = [
        (TipoMovimiento.MERMA, "Merma"),
        (TipoMovimiento.AJUSTE, "Ajuste por conteo"),
        (TipoMovimiento.INICIAL, "Inventario inicial"),
    ]

    tipo = forms.ChoiceField(choices=TIPOS, widget=forms.RadioSelect)
    cantidad = forms.IntegerField(min_value=0)
    nota = forms.CharField(required=False, max_length=255)

    def __init__(self, *args, permitir_inicial=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not permitir_inicial:
            self.fields["tipo"].choices = [
                (valor, etiqueta) for valor, etiqueta in self.TIPOS if valor != TipoMovimiento.INICIAL
            ]

    def clean(self):
        datos = super().clean()
        tipo, cantidad = datos.get("tipo"), datos.get("cantidad")
        if tipo in (TipoMovimiento.MERMA, TipoMovimiento.INICIAL) and cantidad == 0:
            self.add_error("cantidad", "Debe ser al menos 1.")
        return datos
