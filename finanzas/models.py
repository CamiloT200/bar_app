from django.db import models
from django.db.models import F, Q
from django.utils import timezone

# Este módulo no importa modelos de otras apps: CierreCaja encuentra sus
# cuentas, compras y gastos por fecha. Así inventario y ventas pueden importar
# finanzas.periodos sin crear dependencias circulares.


class CategoriaGasto(models.TextChoices):
    ARRIENDO = "ARRIENDO", "Arriendo"
    SERVICIOS = "SERVICIOS", "Servicios"
    NOMINA = "NOMINA", "Nómina"
    MANTENIMIENTO = "MANTENIMIENTO", "Mantenimiento"
    OTRO = "OTRO", "Otro"


class Gasto(models.Model):
    Categoria = CategoriaGasto

    fecha = models.DateTimeField(default=timezone.now)
    categoria = models.CharField(max_length=15, choices=CategoriaGasto.choices)
    descripcion = models.CharField(max_length=255)
    monto = models.PositiveIntegerField()
    pagado_con_caja = models.BooleanField(
        default=True, help_text="El dinero salió del efectivo de la caja del bar."
    )

    class Meta:
        ordering = ["-fecha", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(monto__gt=0), name="gasto_monto_positivo"),
        ]

    def __str__(self):
        return f"{self.get_categoria_display()}: {self.descripcion} ({self.monto})"


CAMPOS_CIERRE = [
    "ventas_efectivo",
    "ventas_otros_medios",
    "compras_efectivo",
    "gastos_efectivo",
    "efectivo_esperado",
    "efectivo_contado",
    "diferencia",
    "cuentas_abiertas_al_cerrar",
]


class CierreCaja(models.Model):
    """Una jornada de caja. Su ventana es [abierto_en, cerrado_en).

    Las ventanas son continuas: cada caja abre en el cerrado_en de la anterior,
    así nada de lo que ocurra entre dos jornadas queda fuera del cuadre.
    """

    abierto_en = models.DateTimeField(help_text="Inicio de la ventana de la jornada.")
    cerrado_en = models.DateTimeField(null=True, blank=True)
    base_inicial = models.PositiveIntegerField()

    # Copia de los totales al cerrar: nulos mientras la caja está abierta.
    ventas_efectivo = models.PositiveIntegerField(null=True, blank=True)
    ventas_otros_medios = models.PositiveIntegerField(null=True, blank=True)
    compras_efectivo = models.PositiveIntegerField(null=True, blank=True)
    gastos_efectivo = models.PositiveIntegerField(null=True, blank=True)
    efectivo_esperado = models.IntegerField(
        null=True, blank=True, help_text="Puede ser negativo si los datos no cuadran."
    )
    efectivo_contado = models.PositiveIntegerField(null=True, blank=True)
    diferencia = models.IntegerField(
        null=True, blank=True, help_text="Contado − esperado: negativo = faltante."
    )
    cuentas_abiertas_al_cerrar = models.PositiveIntegerField(null=True, blank=True)
    observaciones = models.TextField(blank=True)

    class Meta:
        ordering = ["-abierto_en", "-id"]
        verbose_name = "cierre de caja"
        verbose_name_plural = "cierres de caja"
        constraints = [
            # Índice único parcial sobre una expresión constante: solo una fila
            # puede cumplir cerrado_en IS NULL.
            models.UniqueConstraint(
                models.ExpressionWrapper(
                    Q(cerrado_en__isnull=True), output_field=models.BooleanField()
                ),
                condition=Q(cerrado_en__isnull=True),
                name="una_sola_caja_abierta",
            ),
            models.CheckConstraint(
                condition=(
                    Q(cerrado_en__isnull=True, **{f"{c}__isnull": True for c in CAMPOS_CIERRE})
                    | Q(
                        cerrado_en__isnull=False,
                        cerrado_en__gt=F("abierto_en"),
                        **{f"{c}__isnull": False for c in CAMPOS_CIERRE},
                    )
                ),
                name="cierre_caja_estado_coherente",
            ),
        ]

    def __str__(self):
        local = timezone.localtime
        if self.esta_abierta:
            estado = "abierta"
        else:
            estado = f"cerrada {local(self.cerrado_en):%Y-%m-%d %H:%M}"
        return f"Caja {local(self.abierto_en):%Y-%m-%d %H:%M} ({estado})"

    @property
    def esta_abierta(self):
        return self.cerrado_en is None

    @property
    def advertencia(self):
        """Dato informativo: las cuentas abiertas nunca bloquean el cierre."""
        n = self.cuentas_abiertas_al_cerrar
        if not n:
            return ""
        return f"Quedan {n} cuenta(s) abierta(s); su dinero entrará en la jornada en que se cobren."
