"""Apertura y cierre de caja, y registro de gastos.

Todas las funciones corren en transaction.atomic(). Las ventanas de caja son
continuas y semiabiertas: [abierto_en, cerrado_en).
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
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

        # Una cuenta pertenece a la jornada en que se cobró (cerrada_en).
        cuentas = Cuenta.objects.filter(
            estado=EstadoCuenta.CERRADA,
            cerrada_en__gte=cierre.abierto_en,
            cerrada_en__lt=cerrado_en,
        )
        ventana = dict(fecha__gte=cierre.abierto_en, fecha__lt=cerrado_en)

        cierre.ventas_efectivo = _sumar(cuentas.filter(metodo_pago=MetodoPago.EFECTIVO), "total")
        cierre.ventas_otros_medios = _sumar(
            cuentas.exclude(metodo_pago=MetodoPago.EFECTIVO).filter(metodo_pago__isnull=False),
            "total",
        )
        cierre.compras_efectivo = _sumar(
            Compra.objects.filter(pagada_con_caja=True, **ventana), "total"
        )
        cierre.gastos_efectivo = _sumar(
            Gasto.objects.filter(pagado_con_caja=True, **ventana), "monto"
        )
        cierre.efectivo_esperado = (
            cierre.base_inicial
            + cierre.ventas_efectivo
            - cierre.compras_efectivo
            - cierre.gastos_efectivo
        )
        cierre.efectivo_contado = efectivo_contado
        cierre.diferencia = efectivo_contado - cierre.efectivo_esperado
        # Todas las abiertas, sin importar cuándo se abrieron.
        cierre.cuentas_abiertas_al_cerrar = Cuenta.objects.filter(
            estado=EstadoCuenta.ABIERTA
        ).count()
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
