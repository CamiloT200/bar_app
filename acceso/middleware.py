from django.conf import settings
from django.shortcuts import redirect
from django.utils.http import urlencode

from .sesion import sesion_iniciada


class RequiereSesionMiddleware:
    """Exige sesión iniciada en todas las rutas salvo /login/ y /static/.

    Protege todo por defecto: una pantalla nueva nace protegida sin tener que
    acordarse de un decorador. /admin/ también queda detrás (y conserva su
    propio inicio de sesión de Django).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.rutas_libres = (settings.LOGIN_URL, "/" + settings.STATIC_URL.lstrip("/"))

    def __call__(self, request):
        if sesion_iniciada(request) or request.path_info.startswith(self.rutas_libres):
            return self.get_response(request)
        consulta = urlencode({"siguiente": request.get_full_path()})
        return redirect(f"{settings.LOGIN_URL}?{consulta}")
