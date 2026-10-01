"""Única vía para abrir, modificar y cerrar cuentas.

Todas las funciones corren en transaction.atomic(): si algo falla, no queda
nada guardado a medias. El stock se descuenta al agregar un item (el producto
sale de la nevera al servirse), no al cerrar la cuenta.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from finanzas.periodos import verificar_periodo_abierto
from inventario.models import MovimientoInventario, TipoMovimiento

from .models import METODO_MIXTO, Cuenta, EstadoCuenta, ItemConsumo, Mesero, MetodoPago, PagoCuenta


def _es_entero_positivo(valor):
    return isinstance(valor, int) and not isinstance(valor, bool) and valor > 0


def _cuenta_abierta(cuenta):
    """Relee la cuenta de la base de datos y exige que siga abierta."""
    cuenta = Cuenta.objects.get(pk=cuenta.pk)
    if cuenta.estado == EstadoCuenta.ELIMINADA:
        raise ValidationError(f"La cuenta {cuenta.identificador} fue eliminada.")
    if not cuenta.esta_abierta:
        raise ValidationError(f"La cuenta {cuenta.identificador} ya está cerrada.")
    return cuenta


def abrir_cuenta(identificador, mesero=None):
    """Abre una cuenta. El mesero es opcional (hay ventas sin mesero, como en la barra)."""
    identificador = identificador.strip()
    if not identificador:
        raise ValidationError("La cuenta necesita un identificador.")
    if mesero is not None and not mesero.activo:
        raise ValidationError(f"El mesero {mesero.nombre} está inactivo.")
    with transaction.atomic():
        if Cuenta.objects.filter(
            identificador__iexact=identificador, estado=EstadoCuenta.ABIERTA
        ).exists():
            raise ValidationError(f"Ya hay una cuenta abierta llamada {identificador}.")
        return Cuenta.objects.create(identificador=identificador, mesero=mesero)


def crear_mesero(codigo, nombre):
    """El ID del mesero es único sin importar mayúsculas ("m01" == "M01")."""
    codigo, nombre = codigo.strip(), " ".join(nombre.split())
    if not codigo or not nombre:
        raise ValidationError("El mesero necesita ID y nombre.")
    with transaction.atomic():
        existente = Mesero.objects.filter(codigo__iexact=codigo).first()
        if existente:
            pista = "" if existente.activo else " (eliminado de la lista: se puede reactivar en el admin)"
            raise ValidationError(f"El ID {codigo} ya es de {existente.nombre}{pista}.")
        return Mesero.objects.create(codigo=codigo, nombre=nombre)


def eliminar_mesero(mesero):
    """Saca a un mesero de la lista para abrir cuentas.

    - Sin cuentas: se borra del todo; devuelve False.
    - Con cuentas (abiertas o pasadas): se desactiva para que esas cuentas
      conserven su nombre; devuelve True. Las cuentas abiertas que atiende
      siguen con él hasta cobrarse.
    """
    with transaction.atomic():
        mesero = Mesero.objects.get(pk=mesero.pk)
        if not mesero.cuentas.exists():
            mesero.delete()
            return False
        mesero.activo = False
        mesero.save(update_fields=["activo"])
        return True


def agregar_item(cuenta, producto, cantidad, forzar=False, nota_forzado=""):
    """Agrega un item a una cuenta abierta y descuenta el stock.

    Si no hay stock suficiente se rechaza con code="stock_insuficiente" (y
    params stock, cantidad, faltante), salvo que `forzar=True`: entonces se
    exige `nota_forzado` y se crea un AJUSTE positivo solo por el faltante,
    de modo que el stock queda exactamente en 0 tras la venta.
    """
    if not _es_entero_positivo(cantidad):
        raise ValidationError(f"Cantidad inválida: {cantidad}.")
    nota_forzado = nota_forzado.strip()
    if forzar and not nota_forzado:
        raise ValidationError(
            '"Vender de todos modos" requiere una nota.', code="nota_requerida"
        )

    with transaction.atomic():
        verificar_periodo_abierto(timezone.now())
        cuenta = _cuenta_abierta(cuenta)
        if not producto.activo:
            raise ValidationError(f"{producto} está inactivo.")

        stock = producto.stock
        faltante = cantidad - stock
        ajuste = None
        if faltante > 0:
            if not forzar:
                raise ValidationError(
                    "Stock insuficiente de %(producto)s: hay %(stock)s, se piden %(cantidad)s.",
                    code="stock_insuficiente",
                    params={
                        "producto": producto.nombre,
                        "stock": stock,
                        "cantidad": cantidad,
                        "faltante": faltante,
                    },
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
        verificar_periodo_abierto(timezone.now())
        item = ItemConsumo.objects.select_related("cuenta").get(pk=item.pk)
        _cuenta_abierta(item.cuenta)
        if not item.vigente:
            raise ValidationError("Ese item ya fue retirado de la cuenta.")
        _revertir_item(item, item.cuenta, "Retirado de")
        return item


def _revertir_item(item, cuenta, motivo):
    """Devuelve la cantidad del item al stock y lo marca como retirado."""
    item.movimiento_reversion = MovimientoInventario.objects.create(
        producto=item.producto,
        tipo=TipoMovimiento.REVERSION_CONSUMO,
        cantidad=item.cantidad,
        nota=f"{motivo} cuenta #{cuenta.pk} ({cuenta.identificador})",
    )
    item.retirado_en = timezone.now()
    item.save(update_fields=["movimiento_reversion", "retirado_en"])


def eliminar_cuenta(cuenta):
    """Elimina una cuenta abierta y devuelve al stock todo lo que tenía.

    - Si nunca tuvo items, se borra del todo y devuelve (None, 0).
    - Si tuvo items, cada item vigente se revierte (como quitar_item) y la
      cuenta queda en estado ELIMINADA para auditoría. Devuelve
      (cuenta, unidades devueltas).
    Los ajustes de "vender de todos modos" no se revierten, igual que al
    quitar un item. Una cuenta cerrada (ya cobrada) no se puede eliminar.
    """
    with transaction.atomic():
        verificar_periodo_abierto(timezone.now())
        cuenta = _cuenta_abierta(cuenta)
        if not cuenta.items.exists():
            cuenta.delete()
            return None, 0

        devueltas = 0
        for item in cuenta.items_vigentes().select_related("producto"):
            _revertir_item(item, cuenta, "Eliminada")
            devueltas += item.cantidad
        cuenta.estado = EstadoCuenta.ELIMINADA
        cuenta.eliminada_en = timezone.now()
        cuenta.save(update_fields=["estado", "eliminada_en"])
        return cuenta, devueltas


def cerrar_cuenta(cuenta, metodo_pago=None, pagos=None):
    """Fija el total y cómo se pagó. No toca el inventario.

    - `metodo_pago`: todo el total con un solo medio (el caso normal).
    - `pagos`: {medio: monto} para un pago dividido; deben sumar exactamente
      el total. Se guardan como PagoCuenta, que es lo que suma la caja.
    Cuenta.metodo_pago queda como resumen: el medio único o "Mixto".
    """
    if metodo_pago is not None and pagos is not None:
        raise ValidationError("Indica un solo método de pago o un pago dividido, no ambos.")
    if metodo_pago is not None and metodo_pago not in MetodoPago.values:
        raise ValidationError(f"Método de pago inválido: {metodo_pago}.")
    if pagos is not None:
        pagos = {medio: monto for medio, monto in pagos.items() if monto}  # ignora medios en 0
        for medio, monto in pagos.items():
            if medio not in MetodoPago.values:
                raise ValidationError(f"Método de pago inválido: {medio}.")
            if not _es_entero_positivo(monto):
                raise ValidationError(f"Monto inválido para {medio}: {monto}.")

    with transaction.atomic():
        verificar_periodo_abierto(timezone.now())
        cuenta = _cuenta_abierta(cuenta)
        total = cuenta.calcular_total()

        if pagos is None:
            if total > 0 and metodo_pago is None:
                raise ValidationError("Una cuenta con consumo necesita método de pago para cerrarse.")
            pagos = {metodo_pago: total} if total > 0 else {}
            resumen = metodo_pago
        else:
            suma = sum(pagos.values())
            if suma != total:
                diferencia = total - suma
                detalle = f"faltan {diferencia}" if diferencia > 0 else f"sobran {-diferencia}"
                raise ValidationError(
                    f"Los pagos suman {suma} y la cuenta es de {total} ({detalle}).",
                    code="pagos_no_cuadran",
                )
            if len(pagos) == 1:
                resumen = next(iter(pagos))
            else:
                resumen = METODO_MIXTO if pagos else None

        cuenta.total = total
        cuenta.metodo_pago = resumen
        cuenta.cerrada_en = timezone.now()
        cuenta.estado = EstadoCuenta.CERRADA
        cuenta.save(update_fields=["total", "metodo_pago", "cerrada_en", "estado"])
        PagoCuenta.objects.bulk_create(
            PagoCuenta(cuenta=cuenta, metodo=medio, monto=monto) for medio, monto in pagos.items()
        )
        return cuenta
