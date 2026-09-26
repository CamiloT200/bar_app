from django.db import models
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone


class Categoria(models.Model):
    nombre = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "categoría"
        verbose_name_plural = "categorías"

    def __str__(self):
        return self.nombre


class Proveedor(models.Model):
    nombre = models.CharField(max_length=150)
    telefono = models.CharField(max_length=30, blank=True)
    nit = models.CharField("NIT", max_length=30, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name_plural = "proveedores"

    def __str__(self):
        return self.nombre


class ProductoQuerySet(models.QuerySet):
    def con_stock(self):
        """Anota `stock_actual` (suma con signo de los movimientos) en una sola consulta.

        No se llama `stock` para no chocar con la propiedad del modelo.
        """
        return self.annotate(stock_actual=Coalesce(Sum("movimientos__cantidad"), 0))


class Producto(models.Model):
    nombre = models.CharField(max_length=150, unique=True)
    categoria = models.ForeignKey(
        Categoria, on_delete=models.PROTECT, related_name="productos"
    )
    precio_venta = models.PositiveIntegerField(help_text="Precio actual en pesos.")
    stock_minimo = models.PositiveIntegerField(default=0)
    activo = models.BooleanField(default=True)

    objects = ProductoQuerySet.as_manager()

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    @property
    def stock(self):
        """El stock no se guarda: es la suma de los movimientos de inventario."""
        return self.movimientos.aggregate(total=Coalesce(Sum("cantidad"), 0))["total"]


class TipoMovimiento(models.TextChoices):
    INICIAL = "INICIAL", "Inventario inicial"
    COMPRA = "COMPRA", "Compra"
    VENTA = "VENTA", "Venta"
    REVERSION_CONSUMO = "REVERSION_CONSUMO", "Reversión de consumo"
    AJUSTE = "AJUSTE", "Ajuste"
    MERMA = "MERMA", "Merma"


# Se definen a nivel de módulo porque la clase Meta no ve el cuerpo del modelo.
TIPOS_ENTRADA = [TipoMovimiento.INICIAL, TipoMovimiento.COMPRA, TipoMovimiento.REVERSION_CONSUMO]
TIPOS_SALIDA = [TipoMovimiento.VENTA, TipoMovimiento.MERMA]


class MovimientoInventario(models.Model):
    Tipo = TipoMovimiento

    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name="movimientos"
    )
    tipo = models.CharField(max_length=20, choices=TipoMovimiento.choices)
    cantidad = models.IntegerField(help_text="Positiva si entra, negativa si sale.")
    fecha = models.DateTimeField(default=timezone.now)
    nota = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        verbose_name = "movimiento de inventario"
        verbose_name_plural = "movimientos de inventario"
        indexes = [models.Index(fields=["producto", "fecha"])]
        constraints = [
            models.CheckConstraint(
                condition=~Q(cantidad=0),
                name="movimiento_cantidad_distinta_de_cero",
            ),
            models.CheckConstraint(
                condition=(
                    Q(tipo__in=TIPOS_ENTRADA, cantidad__gt=0)
                    | Q(tipo__in=TIPOS_SALIDA, cantidad__lt=0)
                    | Q(tipo=TipoMovimiento.AJUSTE)
                ),
                name="movimiento_signo_coherente_con_tipo",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.cantidad:+d} {self.producto}"


class Compra(models.Model):
    proveedor = models.ForeignKey(
        Proveedor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="compras",
        help_text="Vacío para compras sueltas sin proveedor fijo.",
    )
    fecha = models.DateTimeField(default=timezone.now)
    numero_factura = models.CharField(max_length=50, blank=True)
    total = models.PositiveIntegerField(default=0)
    pagada_con_caja = models.BooleanField(
        default=True, help_text="El dinero salió del efectivo de la caja del bar."
    )

    class Meta:
        ordering = ["-fecha", "-id"]

    def __str__(self):
        return f"Compra #{self.pk} ({self.fecha:%Y-%m-%d})"


class DetalleCompra(models.Model):
    # PROTECT: borrar una compra dejaría huérfanos sus movimientos de inventario.
    compra = models.ForeignKey(Compra, on_delete=models.PROTECT, related_name="detalles")
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name="detalles_compra"
    )
    cantidad = models.PositiveIntegerField()
    costo_unitario = models.PositiveIntegerField()
    movimiento = models.OneToOneField(
        MovimientoInventario, on_delete=models.PROTECT, related_name="detalle_compra"
    )

    class Meta:
        verbose_name = "detalle de compra"
        verbose_name_plural = "detalles de compra"
        constraints = [
            models.CheckConstraint(
                condition=Q(cantidad__gt=0), name="detalle_compra_cantidad_positiva"
            ),
        ]

    def __str__(self):
        return f"{self.cantidad} x {self.producto}"

    @property
    def subtotal(self):
        return self.cantidad * self.costo_unitario
