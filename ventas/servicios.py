"""Única vía para abrir, modificar y cerrar cuentas.

Todas las funciones corren en transaction.atomic(): si algo falla, no queda
nada guardado a medias. El stock se descuenta al agregar un item (el producto
sale de la nevera al servirse), no al cerrar la cuenta.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventario.models import MovimientoInventario, TipoMovimiento

from .models import Cuenta, EstadoCuenta, ItemConsumo, MetodoPago


def _es_entero_positivo(valor):
    return isinstance(valor, int) and not isinstance(valor, bool) and valor > 0


def _cuenta_abierta(cuenta):
    """Relee la cuenta de la base de datos y exige que siga abierta."""
    cuenta = Cuenta.objects.get(pk=cuenta.pk)
    if not cuenta.esta_abierta:
        raise ValidationError(f"La cuenta {cuenta.identificador} ya está cerrada.")
    return cuenta


def abrir_cuenta(identificador):
    identificador = identificador.strip()
    if not identificador:
        raise ValidationError("La cuenta necesita un identificador.")
    with transaction.atomic():
        if Cuenta.objects.filter(
            identificador__iexact=identificador, estado=EstadoCuenta.ABIERTA
        ).exists():
            raise ValidationError(f"Ya hay una cuenta abierta llamada {identificador}.")
        return Cuenta.objects.create(identificador=identificador)


def agregar_item(cuenta, producto, cantidad, forzar=False, nota_forzado=""):
    """Agrega un item a una cuenta abierta y descuenta el stock.

    Si no hay stock suficiente se rechaza, salvo que `forzar=True`: entonces
    se exige `nota_forzado` y se crea un AJUSTE positivo solo por el faltante,
    de modo que el stock queda exactamente en 0 tras la venta.
    """
    if not _es_entero_positivo(cantidad):
        raise ValidationError(f"Cantidad inválida: {cantidad}.")
    nota_forzado = nota_forzado.strip()
    if forzar and not nota_forzado:
        raise ValidationError('"Vender de todos modos" requiere una nota.')

    with transaction.atomic():
        cuenta = _cuenta_abierta(cuenta)
        if not producto.activo:
            raise ValidationError(f"{producto} está inactivo.")

        stock = producto.stock
        faltante = cantidad - stock
        ajuste = None
        if faltante > 0:
            if not forzar:
                raise ValidationError(
                    f"Stock insuficiente de {producto}: hay {stock}, se piden {cantidad}."
                )
            ajuste = MovimientoInventario.objects.create(
                producto=producto,
                tipo=TipoMovimiento.AJUSTE,
                cantidad=faltante,
                nota=nota_forzado,
            )

        movimiento = MovimientoInventario.objects.create(
            producto=producto,
            tipo=TipoMovimiento.VENTA,
            cantidad=-cantidad,
            nota=f"Cuenta #{cuenta.pk} ({cuenta.identificador})",
        )
        return ItemConsumo.objects.create(
            cuenta=cuenta,
            producto=producto,
            cantidad=cantidad,
            precio_unitario=producto.precio_venta,
            movimiento=movimiento,
            movimiento_ajuste=ajuste,
        )


def quitar_item(item):
    """Retira un item de una cuenta abierta y devuelve su cantidad al stock.

    El ajuste forzado, si lo hubo, no se revierte: corrigió un error de conteo
    que sigue corregido aunque el producto vuelva a la nevera.
    """
    with transaction.atomic():
        item = ItemConsumo.objects.select_related("cuenta").get(pk=item.pk)
        _cuenta_abierta(item.cuenta)
        if not item.vigente:
            raise ValidationError("Ese item ya fue retirado de la cuenta.")

        item.movimiento_reversion = MovimientoInventario.objects.create(
            producto=item.producto,
            tipo=TipoMovimiento.REVERSION_CONSUMO,
            cantidad=item.cantidad,
            nota=f"Retirado de cuenta #{item.cuenta.pk} ({item.cuenta.identificador})",
        )
        item.retirado_en = timezone.now()
        item.save(update_fields=["movimiento_reversion", "retirado_en"])
        return item


def cerrar_cuenta(cuenta, metodo_pago=None):
    """Fija el total y el método de pago. No toca el inventario."""
    if metodo_pago is not None and metodo_pago not in MetodoPago.values:
        raise ValidationError(f"Método de pago inválido: {metodo_pago}.")

    with transaction.atomic():
        cuenta = _cuenta_abierta(cuenta)
        total = cuenta.calcular_total()
        if total > 0 and metodo_pago is None:
            raise ValidationError("Una cuenta con consumo necesita método de pago para cerrarse.")

        cuenta.total = total
        cuenta.metodo_pago = metodo_pago
        cuenta.cerrada_en = timezone.now()
        cuenta.estado = EstadoCuenta.CERRADA
        cuenta.save(update_fields=["total", "metodo_pago", "cerrada_en", "estado"])
        return cuenta
