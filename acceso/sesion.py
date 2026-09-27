"""Inicio de sesión de un solo usuario, sin el sistema de usuarios de Django.

El usuario y la clave son valores fijos en settings.py. La app solo escucha en
127.0.0.1: esto evita el uso casual, no pretende resistir un ataque.
"""
import hmac

from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme

CLAVE_SESION = "autenticado"


def credenciales_validas(usuario, clave):
    """Compara en tiempo constante; evalúa ambas para no revelar cuál falló."""
    usuario_ok = hmac.compare_digest(usuario.encode(), settings.BAR_USUARIO.encode())
    clave_ok = hmac.compare_digest(clave.encode(), settings.BAR_CLAVE.encode())
    return usuario_ok and clave_ok


def sesion_iniciada(request):
    return bool(request.session.get(CLAVE_SESION))


def iniciar_sesion(request):
    request.session.cycle_key()  # nueva clave de sesión: evita la fijación de sesión
    request.session[CLAVE_SESION] = True


def siguiente_seguro(request, valor):
    """Solo acepta direcciones internas (evita redirecciones abiertas)."""
    if valor and url_has_allowed_host_and_scheme(
        valor, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return valor
    return None
