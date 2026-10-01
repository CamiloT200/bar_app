"""Vistas de ventas: delgadas. Validan la entrada, llaman a ventas.servicios
y convierten los ValidationError en mensajes. La lógica vive en los servicios.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import F, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from finanzas.servicios import caja_abierta
from inventario.models import Categoria, Producto
from inventario.texto import ordenar_por_nombre

from .forms import CANTIDADES_RAPIDAS, AbrirCuentaForm, AgregarItemForm, CobrarForm, MeseroForm
from .models import METODO_MIXTO, Cuenta, EstadoCuenta, ItemConsumo, Mesero, MetodoPago
from .servicios import (
    abrir_cuenta,
    agregar_item,
    cerrar_cuenta,
    crear_mesero,
    eliminar_cuenta,
    eliminar_mesero,
    quitar_item,
)
from .templatetags.formato import pesos

SESION_DESHACER = "ventas_deshacer_item"

ICONOS_PAGO = {
    MetodoPago.EFECTIVO: "bi-cash-coin",
    MetodoPago.NEQUI: "bi-phone",
    MetodoPago.DAVIPLATA: "bi-phone",
    MetodoPago.TARJETA: "bi-credit-card",
    MetodoPago.TRANSFERENCIA: "bi-bank",
}


def _primer_mensaje(error):
    return error.messages[0] if error.messages else "No se pudo completar la acción."


def _agrupar_items(items):
    """Agrupa items vigentes por (producto, precio) conservando el orden de llegada.

    El botón [−] de cada grupo quita su item más reciente (`ultimo`).
    """
    grupos = {}
    for item in items:
        clave = (item.producto_id, item.precio_unitario)
        grupo = grupos.setdefault(
            clave,
            {"producto": item.producto, "precio": item.precio_unitario, "cantidad": 0, "items": []},
        )
        grupo["cantidad"] += item.cantidad
        grupo["items"].append(item)
    for grupo in grupos.values():
        grupo["subtotal"] = grupo["cantidad"] * grupo["precio"]
        grupo["ultimo"] = grupo["items"][-1]
    return list(grupos.values())


@require_GET
def tablero(request):
    vigentes = Q(items__retirado_en__isnull=True)
    cuentas = (
        Cuenta.objects.filter(estado=EstadoCuenta.ABIERTA)
        .annotate(
            subtotal=Coalesce(
                Sum(F("items__cantidad") * F("items__precio_unitario"), filter=vigentes), 0
            ),
            unidades=Coalesce(Sum("items__cantidad", filter=vigentes), 0),
        )
        .select_related("mesero")
        .order_by("abierta_en", "id")
    )
    # Tras crear un mesero se vuelve aquí con ?mesero=<id> para dejarlo elegido.
    mesero_elegido = request.GET.get("mesero", "")
    return render(
        request,
        "ventas/tablero.html",
        {
            "cuentas": cuentas,
            "hay_caja_abierta": caja_abierta() is not None,
            "meseros": ordenar_por_nombre(Mesero.objects.filter(activo=True)),
            "mesero_elegido": mesero_elegido if mesero_elegido.isdigit() else "",
        },
    )


@require_POST
def abrir(request):
    form = AbrirCuentaForm(request.POST)
    if not form.is_valid():
        if "mesero" in form.errors:
            messages.error(request, "Ese mesero no existe o está inactivo.")
        else:
            messages.error(request, "Escribe un nombre para la cuenta.")
        return redirect("ventas:tablero")
    try:
        nueva = abrir_cuenta(form.cleaned_data["identificador"], mesero=form.cleaned_data["mesero"])
    except ValidationError as e:
        messages.error(request, _primer_mensaje(e))
        return redirect("ventas:tablero")
    return redirect("ventas:cuenta", pk=nueva.pk)


@require_POST
def mesero_crear(request):
    form = MeseroForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Escribe el ID y el nombre del mesero.")
        return redirect("ventas:tablero")
    try:
        mesero = crear_mesero(form.cleaned_data["codigo"], form.cleaned_data["nombre"])
    except ValidationError as e:
        messages.error(request, _primer_mensaje(e))
        return redirect("ventas:tablero")
    messages.success(request, f"Mesero {mesero.nombre} ({mesero.codigo}) agregado.")
    return redirect(f"{reverse('ventas:tablero')}?mesero={mesero.pk}")


@require_POST
def mesero_eliminar(request, pk):
    mesero = get_object_or_404(Mesero, pk=pk)
    conserva_historial = eliminar_mesero(mesero)
    if conserva_historial:
        messages.success(
            request,
            f"Mesero {mesero.nombre} eliminado de la lista. "
            "Sus cuentas conservan su nombre.",
        )
    else:
        messages.success(request, f"Mesero {mesero.nombre} eliminado.")
    return redirect("ventas:tablero")


@require_GET
def cuenta(request, pk):
    cuenta = get_object_or_404(Cuenta, pk=pk)
    items = list(
        cuenta.items_vigentes().select_related("producto").order_by("fecha", "id")
    )
    contexto = {
        "cuenta": cuenta,
        "grupos": _agrupar_items(items),
        "items": items,
        "total": cuenta.total if not cuenta.esta_abierta else sum(i.subtotal for i in items),
    }
    if cuenta.esta_abierta:
        deshacer_pk = request.session.pop(SESION_DESHACER, None)
        contexto.update(
            productos=ordenar_por_nombre(
                Producto.objects.filter(activo=True).con_stock().select_related("categoria")
            ),
            categorias=ordenar_por_nombre(
                Categoria.objects.filter(productos__activo=True).distinct()
            ),
            cantidades=CANTIDADES_RAPIDAS,
            deshacer=next((i for i in items if i.pk == deshacer_pk), None),
        )
    return render(request, "ventas/cuenta.html", contexto)


def _render_forzar(request, cuenta, producto, cantidad, nota="", error=""):
    stock = producto.stock
    return render(
        request,
        "ventas/forzar.html",
        {
            "cuenta": cuenta,
            "producto": producto,
            "cantidad": cantidad,
            "stock": stock,
            "faltante": max(cantidad - stock, 0),
            "nota": nota,
            "error": error,
            "motivos": ["Había en la nevera", "Compra sin registrar", "Conteo desactualizado"],
        },
    )


@require_POST
def agregar(request, pk):
    cuenta = get_object_or_404(Cuenta, pk=pk)
    form = AgregarItemForm(request.POST)
    if not form.is_valid():
        errores_cantidad = form.errors.get("cantidad_otra") or form.errors.get("cantidad")
        messages.error(
            request,
            f"Cantidad: {errores_cantidad[0]}" if errores_cantidad else "Producto o cantidad inválidos.",
        )
        return redirect("ventas:cuenta", pk=cuenta.pk)

    producto = form.cleaned_data["producto"]
    cantidad = form.cleaned_data["cantidad"]
    forzar = form.cleaned_data["forzar"]
    nota = form.cleaned_data["nota"]
    try:
        item = agregar_item(cuenta, producto, cantidad, forzar=forzar, nota_forzado=nota)
    except ValidationError as e:
        codigo = getattr(e, "code", None)
        if codigo == "stock_insuficiente":
            return _render_forzar(request, cuenta, producto, cantidad)
        if codigo == "nota_requerida":
            return _render_forzar(
                request, cuenta, producto, cantidad, nota=nota, error=_primer_mensaje(e)
            )
        messages.error(request, _primer_mensaje(e))
        return redirect("ventas:cuenta", pk=cuenta.pk)

    aviso = f"{item.cantidad} × {producto.nombre} agregada(s)"
    if item.movimiento_ajuste_id:
        aviso += f" (ajuste de inventario +{item.movimiento_ajuste.cantidad} registrado)"
    elif forzar:
        aviso += " (sí había stock: no se registró ajuste)"
    messages.success(request, aviso)
    request.session[SESION_DESHACER] = item.pk
    return redirect("ventas:cuenta", pk=cuenta.pk)


@require_POST
def quitar(request, pk):
    item = get_object_or_404(ItemConsumo.objects.select_related("producto"), pk=pk)
    try:
        quitar_item(item)
    except ValidationError as e:
        messages.error(request, _primer_mensaje(e))
    else:
        messages.success(request, f"Quitado: {item.cantidad} × {item.producto.nombre}")
    return redirect("ventas:cuenta", pk=item.cuenta_id)


@require_POST
def eliminar(request, pk):
    cuenta = get_object_or_404(Cuenta, pk=pk)
    nombre = cuenta.identificador
    try:
        _, devueltas = eliminar_cuenta(cuenta)
    except ValidationError as e:
        messages.error(request, _primer_mensaje(e))
        return redirect("ventas:cuenta", pk=cuenta.pk)
    aviso = f"Cuenta {nombre} eliminada"
    if devueltas:
        aviso += f" ({devueltas} unidad{'es' if devueltas != 1 else ''} devuelta{'s' if devueltas != 1 else ''} al inventario)"
    messages.success(request, aviso + ".")
    return redirect("ventas:tablero")


@require_http_methods(["GET", "POST"])
def cobrar(request, pk):
    cuenta = get_object_or_404(Cuenta, pk=pk)
    if not cuenta.esta_abierta:
        messages.info(
            request, f"La cuenta {cuenta.identificador} está {cuenta.get_estado_display().lower()}."
        )
        return redirect("ventas:cuenta", pk=cuenta.pk)

    if request.method == "POST":
        form = CobrarForm(request.POST)
        if form.is_valid():
            try:
                if "dividido" in request.POST:
                    cerrada = cerrar_cuenta(cuenta, pagos=form.pagos())
                else:
                    cerrada = cerrar_cuenta(cuenta, form.cleaned_data["metodo_pago"] or None)
            except ValidationError as e:
                messages.error(request, _primer_mensaje(e))
            else:
                if cerrada.metodo_pago == METODO_MIXTO:
                    partes = " + ".join(
                        f"{p.get_metodo_display()} {pesos(p.monto)}" for p in cerrada.pagos.all()
                    )
                    messages.success(
                        request, f"{cerrada.identificador} cobrada: {pesos(cerrada.total)} ({partes})"
                    )
                elif cerrada.metodo_pago:
                    messages.success(
                        request,
                        f"{cerrada.identificador} cobrada: {pesos(cerrada.total)} "
                        f"en {cerrada.get_metodo_pago_display()}",
                    )
                else:
                    messages.success(request, f"{cerrada.identificador} cerrada sin cobro.")
                return redirect("ventas:tablero")
        else:
            messages.error(request, "Método de pago o montos inválidos.")
        return redirect("ventas:cobrar", pk=cuenta.pk)

    metodos = [(valor, etiqueta, ICONOS_PAGO[valor]) for valor, etiqueta in MetodoPago.choices]
    return render(
        request,
        "ventas/cobrar.html",
        {
            "cuenta": cuenta,
            "total": cuenta.calcular_total(),
            "metodos": metodos,
            "billetes": [10000, 20000, 50000, 100000],
        },
    )
