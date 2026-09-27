from django.contrib import admin

from inventario.admin import SoloLecturaMixin

from .models import Cuenta, ItemConsumo, Mesero


@admin.register(Mesero)
class MeseroAdmin(admin.ModelAdmin):
    """Aquí se editan o desactivan meseros; se crean también desde el tablero."""

    list_display = ["codigo", "nombre", "activo"]
    list_filter = ["activo"]
    search_fields = ["codigo", "nombre"]

    def has_delete_permission(self, request, obj=None):
        return False  # un mesero con cuentas se desactiva, no se borra


class ItemConsumoInline(SoloLecturaMixin, admin.TabularInline):
    model = ItemConsumo
    fields = ["fecha", "producto", "cantidad", "precio_unitario", "retirado_en"]
    extra = 0


@admin.register(Cuenta)
class CuentaAdmin(SoloLecturaMixin, admin.ModelAdmin):
    """Las cuentas solo se modifican desde ventas/servicios.py."""

    list_display = ["identificador", "mesero", "estado", "abierta_en", "cerrada_en", "metodo_pago", "total"]
    list_filter = ["estado", "metodo_pago", "mesero"]
    search_fields = ["identificador", "mesero__nombre"]
    date_hierarchy = "abierta_en"
    inlines = [ItemConsumoInline]
