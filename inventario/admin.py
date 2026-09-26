from django.contrib import admin

from .models import Categoria, Compra, DetalleCompra, MovimientoInventario, Producto, Proveedor


class SoloLecturaMixin:
    """Compras y movimientos solo se crean desde inventario/servicios.py."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    search_fields = ["nombre"]


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ["nombre", "telefono", "nit", "activo"]
    list_filter = ["activo"]
    search_fields = ["nombre", "nit"]


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "categoria", "precio_venta", "stock", "stock_minimo", "activo"]
    list_filter = ["categoria", "activo"]
    search_fields = ["nombre"]

    def get_queryset(self, request):
        return super().get_queryset(request).con_stock()

    @admin.display(ordering="stock_actual", description="stock")
    def stock(self, obj):
        return obj.stock_actual


@admin.register(MovimientoInventario)
class MovimientoInventarioAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ["fecha", "producto", "tipo", "cantidad", "nota"]
    list_filter = ["tipo", "producto__categoria"]
    search_fields = ["producto__nombre", "nota"]
    date_hierarchy = "fecha"


class DetalleCompraInline(SoloLecturaMixin, admin.TabularInline):
    model = DetalleCompra
    fields = ["producto", "cantidad", "costo_unitario"]
    extra = 0


@admin.register(Compra)
class CompraAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ["id", "fecha", "proveedor", "numero_factura", "total", "pagada_con_caja"]
    list_filter = ["proveedor", "pagada_con_caja"]
    date_hierarchy = "fecha"
    inlines = [DetalleCompraInline]
