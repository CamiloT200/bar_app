"""Única vía para escribir movimientos de inventario y compras.

Todas las funciones corren en transaction.atomic(): si algo falla, no queda
nada guardado a medias.
"""
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from finanzas.periodos import verificar_periodo_abierto

from .models import Compra, DetalleCompra, MovimientoInventario, Producto, TipoMovimiento


@dataclass(frozen=True)
class LineaCompra:
    producto: Producto
    cantidad: int
    costo_unitario: int


def registrar_compra(lineas, proveedor=None, fecha=None, numero_factura="", pagada_con_caja=True):
    """Crea la compra, sus detalles y un movimiento COMPRA positivo por cada línea.

    El total se calcula aquí a partir de las líneas; nunca viene de afuera.
    """
    lineas = list(lineas)
    if not lineas:
        raise ValidationError("La compra debe tener al menos una línea.")
    for linea in lineas:
        if not isinstance(linea.cantidad, int) or linea.cantidad <= 0:
            raise ValidationError(f"Cantidad inválida para {linea.producto}: {linea.cantidad}.")
        if not isinstance(linea.costo_unitario, int) or linea.costo_unitario < 0:
            raise ValidationError(
                f"Costo unitario inválido para {linea.producto}: {linea.costo_unitario}."
            )

    fecha = fecha or timezone.now()
    verificar_periodo_abierto(fecha)
    with transaction.atomic():
        compra = Compra.objects.create(
            proveedor=proveedor,
            fecha=fecha,
            numero_factura=numero_factura,
            pagada_con_caja=pagada_con_caja,
            total=sum(l.cantidad * l.costo_unitario for l in lineas),
        )
        for linea in lineas:
            movimiento = MovimientoInventario.objects.create(
                producto=linea.producto,
                tipo=TipoMovimiento.COMPRA,
                cantidad=linea.cantidad,
                fecha=fecha,
                nota=f"Compra #{compra.pk}",
            )
            DetalleCompra.objects.create(
                compra=compra,
                producto=linea.producto,
                cantidad=linea.cantidad,
                costo_unitario=linea.costo_unitario,
                movimiento=movimiento,
            )
    return compra


TIPOS_MANUALES = [TipoMovimiento.INICIAL, TipoMovimiento.AJUSTE, TipoMovimiento.MERMA]


def registrar_movimiento_manual(producto, tipo, cantidad, nota="", fecha=None):
    """Inventario inicial, ajuste o merma. Ninguna salida puede dejar el stock negativo.

    `cantidad` va con signo: MERMA negativa, INICIAL positiva, AJUSTE cualquiera.
    """
    if tipo not in TIPOS_MANUALES:
        raise ValidationError(f"Tipo {tipo} no se registra manualmente.")
    if not isinstance(cantidad, int) or cantidad == 0:
        raise ValidationError("La cantidad debe ser un entero distinto de cero.")
    if tipo == TipoMovimiento.INICIAL and cantidad < 0:
        raise ValidationError("El inventario inicial debe ser positivo.")
    if tipo == TipoMovimiento.MERMA and cantidad > 0:
        raise ValidationError("Una merma debe ser negativa.")
    if tipo in (TipoMovimiento.AJUSTE, TipoMovimiento.MERMA) and not nota.strip():
        raise ValidationError("Los ajustes y las mermas requieren una nota.", code="nota_requerida")

    with transaction.atomic():
        # Un segundo "inicial" sería un ajuste disfrazado que no exige nota.
        if tipo == TipoMovimiento.INICIAL and producto.movimientos.exists():
            raise ValidationError(
                f"{producto} ya tiene movimientos: usa un ajuste por conteo.",
                code="inicial_repetido",
            )
        if cantidad < 0:
            stock = producto.stock
            if stock + cantidad < 0:
                raise ValidationError(
                    f"Stock insuficiente de {producto}: hay {stock}, se intenta sacar {-cantidad}."
                )
        return MovimientoInventario.objects.create(
            producto=producto,
            tipo=tipo,
            cantidad=cantidad,
            nota=nota.strip(),
            fecha=fecha or timezone.now(),
        )


def ajustar_por_conteo(producto, conteo, nota):
    """Deja el stock exactamente en `conteo` con un AJUSTE por la diferencia.

    La diferencia se calcula aquí, con el stock del momento de guardar: si
    entre tanto se vendió algo, el resultado sigue siendo lo contado.
    """
    if not isinstance(conteo, int) or isinstance(conteo, bool) or conteo < 0:
        raise ValidationError(f"Conteo inválido: {conteo}.")
    if not nota.strip():
        raise ValidationError("Los ajustes requieren una nota.", code="nota_requerida")

    with transaction.atomic():
        diferencia = conteo - producto.stock
        if diferencia == 0:
            raise ValidationError(
                f"El conteo coincide con el stock de {producto}: no hay nada que ajustar.",
                code="sin_diferencia",
            )
        return registrar_movimiento_manual(producto, TipoMovimiento.AJUSTE, diferencia, nota=nota)


def crear_producto(datos, stock_inicial=0):
    """Crea el producto y, si stock_inicial > 0, su movimiento INICIAL.

    Ambos en una sola transacción: si el movimiento falla, el producto
    tampoco queda creado.
    """
    if not isinstance(stock_inicial, int) or isinstance(stock_inicial, bool) or stock_inicial < 0:
        raise ValidationError(f"Inventario inicial inválido: {stock_inicial}.")
    with transaction.atomic():
        producto = Producto.objects.create(**datos)
        if stock_inicial > 0:
            registrar_movimiento_manual(producto, TipoMovimiento.INICIAL, stock_inicial)
        return producto
