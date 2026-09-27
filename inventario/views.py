"""Vistas de inventario: delgadas. Validan la entrada, llaman a
inventario.servicios y muestran los ValidationError en el formulario.
"""
from datetime import timedelta
from itertools import groupby

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, F, OuterRef, Subquery
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from finanzas.servicios import caja_abierta
from ventas.templatetags.formato import pesos

from .forms import (
    CategoriaForm,
    CompraForm,
    LineaCompraFormSet,
    MovimientoForm,
    ProductoForm,
    ProductoNuevoForm,
)
from .models import Categoria, Compra, DetalleCompra, Producto, TipoMovimiento
from .texto import normalizar_nombre, ordenar_por_nombre
from .servicios import (
    LineaCompra,
    ajustar_por_conteo,
    crear_producto,
    registrar_compra,
    registrar_movimiento_manual,
)

DIAS_LISTA_COMPRAS = 30

MOTIVOS = {
    TipoMovimiento.MERMA: ["Se rompió", "Se venció", "Consumo de la casa", "Cortesía"],
    TipoMovimiento.AJUSTE: ["Conteo físico"],
}


def _agregar_errores(form, error):
    """Pasa los mensajes de un ValidationError del servicio al formulario."""
    for mensaje in error.messages:
        form.add_error(None, mensaje)


def _en_alerta(queryset):
    return queryset.filter(stock_actual__lte=F("stock_minimo"))


# --- Productos ---------------------------------------------------------------


@require_GET
def productos(request):
    categoria = request.GET.get("categoria", "")
    solo_alertas = request.GET.get("alerta") == "1"
    con_inactivos = request.GET.get("inactivos") == "1"

    lista = Producto.objects.con_stock().select_related("categoria")
    if not con_inactivos:
        lista = lista.filter(activo=True)
    if categoria.isdigit():
        lista = lista.filter(categoria_id=int(categoria))
    if solo_alertas:
        lista = _en_alerta(lista)

    def url_con(**cambios):
        params = {"categoria": categoria, "alerta": "1" if solo_alertas else "",
                  "inactivos": "1" if con_inactivos else ""}
        params.update(cambios)
        consulta = "&".join(f"{k}={v}" for k, v in params.items() if v)
        return f"?{consulta}" if consulta else "?"

    return render(
        request,
        "inventario/productos.html",
        {
            "productos": ordenar_por_nombre(lista),
            "alertas": _en_alerta(Producto.objects.filter(activo=True).con_stock()).count(),
            "categorias": [
                (c, url_con(categoria=str(c.pk)), str(c.pk) == categoria)
                for c in ordenar_por_nombre(Categoria.objects.all())
            ],
            "url_todas": url_con(categoria=""),
            "categoria_activa": categoria,
            "solo_alertas": solo_alertas,
            "con_inactivos": con_inactivos,
            "url_alertas": url_con(alerta="" if solo_alertas else "1"),
            "url_solo_alertas": url_con(alerta="1", categoria=""),
            "url_inactivos": url_con(inactivos="" if con_inactivos else "1"),
        },
    )


@require_http_methods(["GET", "POST"])
def producto_nuevo(request):
    form = ProductoNuevoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        datos = dict(form.cleaned_data)
        stock_inicial = datos.pop("stock_inicial") or 0
        try:
            producto = crear_producto(datos, stock_inicial=stock_inicial)
        except ValidationError as e:
            _agregar_errores(form, e)
        else:
            aviso = f"Producto {producto.nombre} creado"
            if stock_inicial:
                aviso += f" con inventario inicial de {stock_inicial}"
            messages.success(request, aviso + ".")
            return redirect("inventario:productos")
    return render(request, "inventario/producto_form.html", {"form": form, "producto": None})


@require_http_methods(["GET", "POST"])
def producto_editar(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    form = ProductoForm(request.POST or None, instance=producto)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Producto {producto.nombre} actualizado.")
        return redirect("inventario:productos")
    return render(
        request,
        "inventario/producto_form.html",
        {"form": form, "producto": producto, "stock": producto.stock},
    )


@require_http_methods(["GET", "POST"])
def producto_movimiento(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    permitir_inicial = not producto.movimientos.exists()
    form = MovimientoForm(request.POST or None, permitir_inicial=permitir_inicial)

    if request.method == "POST" and form.is_valid():
        tipo = form.cleaned_data["tipo"]
        cantidad = form.cleaned_data["cantidad"]
        nota = form.cleaned_data["nota"]
        try:
            if tipo == TipoMovimiento.AJUSTE:
                movimiento = ajustar_por_conteo(producto, cantidad, nota)
            elif tipo == TipoMovimiento.MERMA:
                movimiento = registrar_movimiento_manual(producto, tipo, -cantidad, nota=nota)
            else:
                movimiento = registrar_movimiento_manual(producto, tipo, cantidad, nota=nota)
        except ValidationError as e:
            _agregar_errores(form, e)
        else:
            descripcion = {
                TipoMovimiento.MERMA: f"merma de {cantidad}",
                TipoMovimiento.AJUSTE: f"ajuste de {movimiento.cantidad:+d}",
                TipoMovimiento.INICIAL: f"inventario inicial de {cantidad}",
            }[tipo]
            messages.success(
                request, f"{producto.nombre}: se registró {descripcion} (stock {producto.stock})."
            )
            return redirect("inventario:productos")

    return render(
        request,
        "inventario/movimiento.html",
        {
            "producto": producto,
            "stock": producto.stock,
            "form": form,
            # Un producto sin movimientos casi siempre viene por su inventario inicial.
            "tipo_elegido": form["tipo"].value()
            or (TipoMovimiento.INICIAL if permitir_inicial else TipoMovimiento.MERMA),
            "tipos": form.fields["tipo"].choices,
            "motivos": [(tipo, m) for tipo, lista in MOTIVOS.items() for m in lista],
        },
    )


# --- Categorías --------------------------------------------------------------


@require_GET
def categorias(request):
    lista = ordenar_por_nombre(Categoria.objects.annotate(num_productos=Count("productos")))
    return render(request, "inventario/categorias.html", {"categorias": lista, "form": CategoriaForm()})


def _primer_error(form):
    return next(iter(e for errores in form.errors.values() for e in errores), "Datos inválidos.")


@require_POST
def categoria_crear(request):
    form = CategoriaForm(request.POST)
    if form.is_valid():
        categoria = form.save()
        messages.success(request, f"Categoría {categoria.nombre} creada.")
    else:
        messages.error(request, _primer_error(form))
    return redirect("inventario:categorias")


@require_POST
def categoria_renombrar(request, pk):
    categoria = get_object_or_404(Categoria, pk=pk)
    anterior = categoria.nombre
    form = CategoriaForm(request.POST, instance=categoria)
    if form.is_valid():
        form.save()
        messages.success(request, f"Categoría {anterior} renombrada a {categoria.nombre}.")
    else:
        messages.error(request, _primer_error(form))
    return redirect("inventario:categorias")


@require_POST
def categoria_borrar(request, pk):
    categoria = get_object_or_404(Categoria, pk=pk)
    try:
        categoria.delete()
    except ProtectedError:
        messages.error(request, f"{categoria.nombre} tiene productos: no se puede borrar.")
    else:
        messages.success(request, f"Categoría {categoria.nombre} borrada.")
    return redirect("inventario:categorias")


# --- Compras -----------------------------------------------------------------


@require_GET
def compras(request):
    desde = timezone.now() - timedelta(days=DIAS_LISTA_COMPRAS)
    lista = (
        Compra.objects.filter(fecha__gte=desde)
        .select_related("proveedor")
        .annotate(num_lineas=Count("detalles"))
        .order_by("-fecha", "-id")
    )
    return render(request, "inventario/compras.html", {"compras": lista, "dias": DIAS_LISTA_COMPRAS})


def _productos_para_compra():
    """Productos activos agrupados por categoría, con su último costo de compra."""
    ultimo_costo = (
        DetalleCompra.objects.filter(producto=OuterRef("pk"))
        .order_by("-compra__fecha", "-id")
        .values("costo_unitario")[:1]
    )
    productos = sorted(
        Producto.objects.filter(activo=True)
        .select_related("categoria")
        .annotate(ultimo_costo=Subquery(ultimo_costo)),
        key=lambda p: (normalizar_nombre(p.categoria.nombre), p.categoria_id, normalizar_nombre(p.nombre)),
    )
    return [(categoria, list(grupo)) for categoria, grupo in groupby(productos, key=lambda p: p.categoria)]


@require_http_methods(["GET", "POST"])
def compra_nueva(request):
    form = CompraForm(request.POST or None)
    lineas = LineaCompraFormSet(request.POST or None, prefix="lineas")

    if request.method == "POST" and form.is_valid() and lineas.is_valid():
        datos = form.cleaned_data
        lineas_llenas = [
            LineaCompra(l["producto"], l["cantidad"], l["costo_unitario"])
            for l in lineas.cleaned_data
            if l
        ]
        try:
            compra = registrar_compra(
                lineas_llenas,
                proveedor=datos["proveedor"],
                fecha=datos["fecha"],
                numero_factura=datos["numero_factura"],
                pagada_con_caja=datos["pagada_con_caja"],
            )
        except ValidationError as e:
            _agregar_errores(form, e)
        else:
            messages.success(
                request,
                f"Compra registrada: {pesos(compra.total)} ({len(lineas_llenas)} "
                f"línea{'s' if len(lineas_llenas) != 1 else ''}).",
            )
            return redirect("inventario:compra", pk=compra.pk)

    return render(
        request,
        "inventario/compra_form.html",
        {
            "form": form,
            "lineas": lineas,
            "grupos_productos": _productos_para_compra(),
            "hay_caja_abierta": caja_abierta() is not None,
        },
    )


@require_GET
def compra(request, pk):
    compra = get_object_or_404(Compra.objects.select_related("proveedor"), pk=pk)
    detalles = compra.detalles.select_related("producto").order_by("id")
    return render(request, "inventario/compra_detalle.html", {"compra": compra, "detalles": detalles})
