from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .sesion import credenciales_validas, iniciar_sesion, sesion_iniciada, siguiente_seguro


@require_http_methods(["GET", "POST"])
def login(request):
    siguiente = siguiente_seguro(
        request, request.POST.get("siguiente") or request.GET.get("siguiente")
    )
    if sesion_iniciada(request):
        return redirect(siguiente or "ventas:tablero")

    usuario, error = "", ""
    if request.method == "POST":
        usuario = request.POST.get("usuario", "").strip()
        if credenciales_validas(usuario, request.POST.get("clave", "")):
            iniciar_sesion(request)
            return redirect(siguiente or "ventas:tablero")
        # Sin decir cuál de los dos falló. La clave no se devuelve al formulario.
        error = "Usuario o contraseña incorrectos."

    return render(
        request,
        "acceso/login.html",
        {"usuario": usuario, "error": error, "siguiente": siguiente or ""},
    )


@require_POST
def logout(request):
    request.session.flush()
    return redirect("acceso:login")
