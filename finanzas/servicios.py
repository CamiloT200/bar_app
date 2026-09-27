"""Apertura y cierre de caja, y registro de gastos.

Todas las funciones corren en transaction.atomic(). Las ventanas de caja son
continuas y semiabiertas: [abierto_en, cerrado_en).
"""
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from inventario.models import Compra
from ventas.models import Cuenta, EstadoCuenta, MetodoPago

from .models import CategoriaGasto, CierreCaja, Gasto
from .periodos import verificar_periodo_abierto


def _es_entero(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def _sumar(queryset, campo):
    return queryset.aggregate(total=Coalesce(Sum(campo), 0))["total"]


def caja_abierta():
    return CierreCaja.objects.filter(cerrado_en__isnull=True).first()


def ventana_actual():
    """(caja abierta o None, inicio de la ventana en curso o None).

    Con ventanas continuas, sin caja abierta la ventana empieza en el último
    cierre: lo que se registre desde entonces entrará en la próxima jornada.
    None si nunca se ha abierto una caja.
    """
    caja = caja_abierta()
    if caja is not None:
        return caja, caja.abierto_en
    ultimo = CierreCaja.objects.filter(cerrado_en__isnull=False).order_by("-cerrado_en").first()
    return None, ultimo.cerrado_en if ultimo else None


@dataclass(frozen=True)
class DetalleVentana:
    """Lo que entra en la ventana [desde, hasta). Base de resumen y listados."""

    cuentas_cobradas: QuerySet  # una cuenta pertenece a la jornada en que se cobró
    compras_con_caja: QuerySet
    gastos_con_caja: QuerySet


def detalle_ventana(desde, hasta):
    ventana = dict(fecha__gte=desde, fecha__lt=hasta)
    return DetalleVentana(
        cuentas_cobradas=Cuenta.objects.filter(
            estado=EstadoCuenta.CERRADA, cerrada_en__gte=desde, cerrada_en__lt=hasta
        ).order_by("cerrada_en", "id"),
        compras_con_caja=Compra.objects.filter(pagada_con_caja=True, **ventana)
        .select_related("proveedor")
        .order_by("fecha", "id"),
        gastos_con_caja=Gasto.objects.filter(pagado_con_caja=True, **ventana).order_by("fecha", "id"),
    )


@dataclass(frozen=True)
class Resumen:
    ventas_efectivo: int
    ventas_otros_medios: int
    compras_efectivo: int
    gastos_efectivo: int
    cuentas_abiertas: int

    def efectivo_esperado(self, base_inicial):
        return base_inicial + self.ventas_efectivo - self.compras_efectivo - self.gastos_efectivo

    @property
    def cifras(self):
        """Huella de las cuatro cifras, para detectar si cambiaron."""
        return (
            f"{self.ventas_efectivo}-{self.ventas_otros_medios}-"
            f"{self.compras_efectivo}-{self.gastos_efectivo}"
        )


def resumen_ventana(desde, hasta):
    """Las cifras del cierre para [desde, hasta). Único cálculo: lo usan la
    pantalla de caja (en vivo) y cerrar_caja (copia definitiva)."""
    detalle = detalle_ventana(desde, hasta)
    cuentas = detalle.cuentas_cobradas
    return Resumen(
        ventas_efectivo=_sumar(cuentas.filter(metodo_pago=MetodoPago.EFECTIVO), "total"),
        ventas_otros_medios=_sumar(
            cuentas.exclude(metodo_pago=MetodoPago.EFECTIVO).filter(metodo_pago__isnull=False),
            "total",
        ),
        compras_efectivo=_sumar(detalle.compras_con_caja, "total"),
        gastos_efectivo=_sumar(detalle.gastos_con_caja, "monto"),
        # Todas las abiertas, sin importar cuándo se abrieron.
        cuentas_abiertas=Cuenta.objects.filter(estado=EstadoCuenta.ABIERTA).count(),
    )


def abrir_caja(base_inicial):
    """Abre la jornada. Su ventana empieza donde terminó la anterior."""
    if not _es_entero(base_inicial) or base_inicial < 0:
        raise ValidationError(f"Base inicial inválida: {base_inicial}.")
    with transaction.atomic():
        if caja_abierta() is not None:
            raise ValidationError("Ya hay una caja abierta; ciérrala antes de abrir otra.")
        ultima = CierreCaja.objects.filter(cerrado_en__isnull=False).order_by("-cerrado_en").first()
        abierto_en = ultima.cerrado_en if ultima else timezone.now()
        return CierreCaja.objects.create(abierto_en=abierto_en, base_inicial=base_inicial)


def cerrar_caja(efectivo_contado, observaciones=""):
    """Cierra la jornada y guarda una copia de los totales.

    Las cuentas abiertas no bloquean el cierre: solo se cuentan en
    `cuentas_abiertas_al_cerrar` (ver CierreCaja.advertencia).
    """
    if not _es_entero(efectivo_contado) or efectivo_contado < 0:
        raise ValidationError(f"Efectivo contado inválido: {efectivo_contado}.")

    with transaction.atomic():
        cierre = caja_abierta()
        if cierre is None:
            raise ValidationError("No hay caja abierta para cerrar.")
        cerrado_en = timezone.now()
        if cerrado_en <= cierre.abierto_en:
            raise ValidationError("La hora de cierre es anterior a la apertura; revisa el reloj.")

        resumen = resumen_ventana(cierre.abierto_en, cerrado_en)
        cierre.ventas_efectivo = resumen.ventas_efectivo
        cierre.ventas_otros_medios = resumen.ventas_otros_medios
        cierre.compras_efectivo = resumen.compras_efectivo
        cierre.gastos_efectivo = resumen.gastos_efectivo
        cierre.efectivo_esperado = resumen.efectivo_esperado(cierre.base_inicial)
        cierre.efectivo_contado = efectivo_contado
        cierre.diferencia = efectivo_contado - cierre.efectivo_esperado
        cierre.cuentas_abiertas_al_cerrar = resumen.cuentas_abiertas
        cierre.observaciones = observaciones.strip()
        cierre.cerrado_en = cerrado_en
        cierre.save()
        return cierre


def registrar_gasto(categoria, descripcion, monto, pagado_con_caja=True, fecha=None):
    if categoria not in CategoriaGasto.values:
        raise ValidationError(f"Categoría de gasto inválida: {categoria}.")
    descripcion = descripcion.strip()
    if not descripcion:
        raise ValidationError("El gasto necesita una descripción.")
    if not _es_entero(monto) or monto <= 0:
        raise ValidationError(f"Monto inválido: {monto}.")

    fecha = fecha or timezone.now()
    verificar_periodo_abierto(fecha)
    with transaction.atomic():
        return Gasto.objects.create(
            fecha=fecha,
            categoria=categoria,
            descripcion=descripcion,
            monto=monto,
            pagado_con_caja=pagado_con_caja,
        )
