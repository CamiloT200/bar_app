from django.contrib import admin

from inventario.admin import SoloLecturaMixin

from .models import CierreCaja, ConteoMedioPago, Gasto


@admin.register(Gasto)
class GastoAdmin(SoloLecturaMixin, admin.ModelAdmin):
    """Los gastos solo se crean con finanzas.servicios.registrar_gasto."""

    list_display = ["fecha", "categoria", "descripcion", "monto", "pagado_con_caja"]
    list_filter = ["categoria", "pagado_con_caja"]
    search_fields = ["descripcion"]
    date_hierarchy = "fecha"


class ConteoMedioPagoInline(SoloLecturaMixin, admin.TabularInline):
    model = ConteoMedioPago
    fields = ["metodo", "esperado", "contado", "diferencia"]
    extra = 0


@admin.register(CierreCaja)
class CierreCajaAdmin(SoloLecturaMixin, admin.ModelAdmin):
    inlines = [ConteoMedioPagoInline]
    list_display = [
        "abierto_en",
        "cerrado_en",
        "base_inicial",
        "ventas_efectivo",
        "ventas_otros_medios",
        "efectivo_esperado",
        "efectivo_contado",
        "diferencia",
        "cuentas_abiertas_al_cerrar",
    ]
    date_hierarchy = "abierto_en"
