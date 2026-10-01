from django.db import models
from django.db.models import F, Q, Sum
from django.db.models.functions import Coalesce, Lower
from django.utils import timezone

from inventario.models import MovimientoInventario, Producto


class EstadoCuenta(models.TextChoices):
    ABIERTA = "ABIERTA", "Abierta"
    CERRADA = "CERRADA", "Cerrada"
    ELIMINADA = "ELIMINADA", "Eliminada"


class MetodoPago(models.TextChoices):
    EFECTIVO = "EFECTIVO", "Efectivo"
    NEQUI = "NEQUI", "Nequi"
    DAVIPLATA = "DAVIPLATA", "Daviplata"
    TARJETA = "TARJETA", "Tarjeta"
    TRANSFERENCIA = "TRANSFERENCIA", "Transferencia"


# Cuenta.metodo_pago es un resumen: el medio único o "Mixto" si se dividió.
# El detalle (fuente de verdad para la caja) está en PagoCuenta.
METODO_MIXTO = "MIXTO"
METODOS_CUENTA = MetodoPago.choices + [(METODO_MIXTO, "Mixto")]


class Mesero(models.Model):
    codigo = models.CharField(
        "ID del mesero", max_length=20, unique=True, help_text='Código propio, ej. "M01".'
    )
    nombre = models.CharField(max_length=100)
    activo = models.BooleanField(default=True, help_text="Los inactivos no aparecen al abrir cuentas.")

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.nombre} ({self.codigo})"


class Cuenta(models.Model):
    Estado = EstadoCuenta
    MetodoPago = MetodoPago

    identificador = models.CharField(max_length=100, help_text='Texto libre, ej. "Mesa 3".')
    # Opcional: también hay ventas sin mesero (por ejemplo, en la barra).
    mesero = models.ForeignKey(
        Mesero, on_delete=models.PROTECT, null=True, blank=True, related_name="cuentas"
    )
    abierta_en = models.DateTimeField(default=timezone.now)
    cerrada_en = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(
        max_length=10, choices=EstadoCuenta.choices, default=EstadoCuenta.ABIERTA
    )
    metodo_pago = models.CharField(
        max_length=15, choices=METODOS_CUENTA, null=True, blank=True
    )
    total = models.PositiveIntegerField(
        null=True, blank=True, help_text="Se fija al cerrar la cuenta."
    )
    # Una cuenta eliminada con consumo no se borra: queda para auditoría.
    eliminada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-abierta_en", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        estado=EstadoCuenta.ABIERTA,
                        cerrada_en__isnull=True,
                        eliminada_en__isnull=True,
                        metodo_pago__isnull=True,
                        total__isnull=True,
                    )
                    | Q(
                        estado=EstadoCuenta.CERRADA,
                        cerrada_en__isnull=False,
                        eliminada_en__isnull=True,
                        total__isnull=False,
                    )
                    | Q(
                        estado=EstadoCuenta.ELIMINADA,
                        eliminada_en__isnull=False,
                        cerrada_en__isnull=True,
                        metodo_pago__isnull=True,
                        total__isnull=True,
                    )
                ),
                name="cuenta_estado_coherente",
            ),
            models.CheckConstraint(
                condition=Q(total__isnull=True) | Q(total=0) | Q(metodo_pago__isnull=False),
                name="cuenta_con_total_tiene_metodo_pago",
            ),
            # "Mesa 3" y "mesa 3" son la misma mesa.
            models.UniqueConstraint(
                Lower("identificador"),
                condition=Q(estado=EstadoCuenta.ABIERTA),
                name="cuenta_abierta_identificador_unico",
            ),
        ]

    def __str__(self):
        return f"{self.identificador} ({self.get_estado_display()})"

    @property
    def esta_abierta(self):
        return self.estado == EstadoCuenta.ABIERTA

    def items_vigentes(self):
        return self.items.filter(retirado_en__isnull=True)

    def calcular_total(self):
        """Suma de los subtotales de los items no retirados."""
        return self.items_vigentes().aggregate(
            total=Coalesce(Sum(F("cantidad") * F("precio_unitario")), 0)
        )["total"]


class ItemConsumo(models.Model):
    cuenta = models.ForeignKey(Cuenta, on_delete=models.PROTECT, related_name="items")
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name="items_consumo"
    )
    cantidad = models.PositiveIntegerField()
    precio_unitario = models.PositiveIntegerField(
        help_text="Copiado de producto.precio_venta al agregar el item."
    )
    fecha = models.DateTimeField(default=timezone.now)
    movimiento = models.OneToOneField(
        MovimientoInventario, on_delete=models.PROTECT, related_name="item_consumo"
    )
    movimiento_ajuste = models.OneToOneField(
        MovimientoInventario,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="item_consumo_forzado",
        help_text='Ajuste creado por "vender de todos modos".',
    )
    retirado_en = models.DateTimeField(null=True, blank=True)
    movimiento_reversion = models.OneToOneField(
        MovimientoInventario,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="item_consumo_revertido",
    )

    class Meta:
        ordering = ["fecha", "id"]
        verbose_name = "item de consumo"
        verbose_name_plural = "items de consumo"
        constraints = [
            models.CheckConstraint(
                condition=Q(cantidad__gt=0), name="item_consumo_cantidad_positiva"
            ),
            models.CheckConstraint(
                condition=(
                    Q(retirado_en__isnull=True, movimiento_reversion__isnull=True)
                    | Q(retirado_en__isnull=False, movimiento_reversion__isnull=False)
                ),
                name="item_consumo_retiro_coherente",
            ),
        ]

    def __str__(self):
        return f"{self.cantidad} x {self.producto} ({self.cuenta.identificador})"

    @property
    def subtotal(self):
        return self.cantidad * self.precio_unitario

    @property
    def vigente(self):
        return self.retirado_en is None


class PagoCuenta(models.Model):
    """Una parte del pago de una cuenta cerrada. La suma de los pagos de una
    cuenta es su total (lo garantiza cerrar_cuenta). Un medio por cuenta."""

    cuenta = models.ForeignKey(Cuenta, on_delete=models.PROTECT, related_name="pagos")
    metodo = models.CharField(max_length=15, choices=MetodoPago.choices)
    monto = models.PositiveIntegerField()

    class Meta:
        ordering = ["id"]
        verbose_name = "pago de cuenta"
        verbose_name_plural = "pagos de cuentas"
        constraints = [
            models.CheckConstraint(condition=Q(monto__gt=0), name="pago_cuenta_monto_positivo"),
            models.UniqueConstraint(fields=["cuenta", "metodo"], name="pago_cuenta_un_medio_por_cuenta"),
        ]

    def __str__(self):
        return f"{self.get_metodo_display()} {self.monto} ({self.cuenta.identificador})"
