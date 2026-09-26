from django.contrib import admin

from inventario.admin import SoloLecturaMixin

from .models import Cuenta, ItemConsumo


class ItemConsumoInline(SoloLecturaMixin, admin.TabularInline):
    model = ItemConsumo
    fields = ["fecha", "producto", "cantidad", "precio_unitario", "retirado_en"]
    extra = 0


@admin.register(Cuenta)
class CuentaAdmin(SoloLecturaMixin, admin.ModelAdmin):
    """Las cuentas solo se modifican desde ventas/servicios.py."""

    list_display = ["identificador", "estado", "abierta_en", "cerrada_en", "metodo_pago", "total"]
    list_filter = ["estado", "metodo_pago"]
    search_fields = ["identificador"]
    date_hierarchy = "abierta_en"
    inlines = [ItemConsumoInline]
