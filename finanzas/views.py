"""Vistas de finanzas: delgadas. Validan la entrada, llaman a
finanzas.servicios y muestran los ValidationError al usuario.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ventas.models import Cuenta, EstadoCuenta
from ventas.templatetags.formato import pesos

from .forms import AbrirCajaForm, CerrarCajaForm, GastoForm
from .models import CierreCaja
from .servicios import (
    abrir_caja,
    caja_abierta,
    cerrar_caja,
    detalle_ventana,
    registrar_gasto,
    resumen_ventana,
    ventana_actual,
)

CIERRES_POR_PAGINA = 30

# Denominaciones colombianas para contar el cajón al cerrar.
BILLETES = [100000, 50000, 20000, 10000, 5000, 2000, 1000]


def _primer_mensaje(error):
    return error.messages[0] if error.messages else "No se pudo completar la acción."


def _siguiente_seguro(request, valor):
    """Solo acepta direcciones internas (evita redirecciones abiertas)."""
    if valor and url_has_allowed_host_and_scheme(
        valor, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return valor
    return None


def _cuentas_abiertas():
    return Cuenta.objects.filter(estado=EstadoCuenta.ABIERTA).order_by("abierta_en", "id")


def _texto_diferencia(diferencia):
    if diferencia < 0:
        return f"faltan {pesos(-diferencia)}"
    if diferencia > 0:
        return f"sobran {pesos(diferencia)}"
    return "cuadra exacto"


@require_GET
def caja(request):
    caja, desde = ventana_actual()
    ahora = timezone.now()
    contexto = {
        "caja": caja,
        "desde": desde,
        "resumen": resumen_ventana(desde, ahora) if desde else None,
        "siguiente": _siguiente_seguro(request, request.GET.get("siguiente")) or "",
        "form": AbrirCajaForm(),
    }
    if caja:
        contexto.update(
            detalle=detalle_ventana(desde, ahora),
            cuentas_abiertas=_cuentas_abiertas(),
            esperado=contexto["resumen"].efectivo_esperado(caja.base_inicial),
        )
    return render(request, "finanzas/caja.html", contexto)


@require_POST
def abrir(request):
    siguiente = _siguiente_seguro(request, request.POST.get("siguiente"))
    form = AbrirCajaForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Escribe la base inicial (un número de 0 en adelante).")
    else:
        try:
            nueva = abrir_caja(form.cleaned_data["base_inicial"])
        except ValidationError as e:
            messages.error(request, _primer_mensaje(e))
        else:
            messages.success(request, f"Caja abierta con base {pesos(nueva.base_inicial)}.")
            return redirect(siguiente or "finanzas:caja")
    url = reverse("finanzas:caja")
    return redirect(f"{url}?{urlencode({'siguiente': siguiente})}" if siguiente else url)


@require_http_methods(["GET", "POST"])
def cerrar(request):
    caja = caja_abierta()
    if caja is None:
        messages.info(request, "No hay caja abierta para cerrar.")
        return redirect("finanzas:caja")

    form = CerrarCajaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            cierre = cerrar_caja(
                form.cleaned_data["efectivo_contado"], form.cleaned_data["observaciones"]
            )
        except ValidationError as e:
            form.add_error(None, _primer_mensaje(e))
        else:
            messages.success(
                request, f"Caja cerrada: {_texto_diferencia(cierre.diferencia)}."
            )
            previas = form.cleaned_data["cifras_previas"]
            actuales = resumen_ventana(cierre.abierto_en, cierre.cerrado_en).cifras
            if previas and previas != actuales:
                messages.warning(
                    request,
                    "Las cifras cambiaron desde que abriste la pantalla de cierre "
                    "(por ejemplo, se cobró otra cuenta). El cierre usa las del momento de cerrar.",
                )
            return redirect("finanzas:cierre", pk=cierre.pk)

    resumen = resumen_ventana(caja.abierto_en, timezone.now())
    return render(
        request,
        "finanzas/cerrar.html",
        {
            "caja": caja,
            "form": form,
            "resumen": resumen,
            "esperado": resumen.efectivo_esperado(caja.base_inicial),
            "cuentas_abiertas": _cuentas_abiertas(),
            "billetes": BILLETES,
        },
    )


@require_http_methods(["GET", "POST"])
def gasto_nuevo(request):
    form = GastoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        try:
            gasto = registrar_gasto(
                datos["categoria"],
                datos["descripcion"],
                datos["monto"],
                pagado_con_caja=datos["pagado_con_caja"],
                fecha=datos["fecha"],
            )
        except ValidationError as e:
            form.add_error(None, _primer_mensaje(e))
        else:
            messages.success(
                request,
                f"Gasto registrado: {pesos(gasto.monto)} "
                f"({gasto.get_categoria_display()}: {gasto.descripcion}).",
            )
            return redirect("finanzas:caja")
    return render(
        request,
        "finanzas/gasto_form.html",
        {
            "form": form,
            "categoria_elegida": form["categoria"].value() or "",
            "hay_caja_abierta": caja_abierta() is not None,
        },
    )


@require_GET
def cierres(request):
    cerrados = CierreCaja.objects.filter(cerrado_en__isnull=False).order_by("-cerrado_en")
    pagina = Paginator(cerrados, CIERRES_POR_PAGINA).get_page(request.GET.get("pagina"))
    return render(
        request,
        "finanzas/cierres.html",
        {
            "pagina": pagina,
            "total_diferencias": sum(c.diferencia for c in pagina),
            "total_ventas": sum(c.ventas_efectivo + c.ventas_otros_medios for c in pagina),
        },
    )


@require_GET
def cierre(request, pk):
    cierre = get_object_or_404(CierreCaja, pk=pk)
    if cierre.esta_abierta:
        return redirect("finanzas:caja")
    return render(
        request,
        "finanzas/cierre.html",
        {"cierre": cierre, "detalle": detalle_ventana(cierre.abierto_en, cierre.cerrado_en)},
    )
