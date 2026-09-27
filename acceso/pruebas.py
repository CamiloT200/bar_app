"""Ayuda para las pruebas de vistas de las demás apps."""
from django.conf import settings
from django.urls import reverse


def iniciar_sesion(client):
    """Inicia sesión por el camino real (POST a /login/)."""
    respuesta = client.post(
        reverse("acceso:login"), {"usuario": settings.BAR_USUARIO, "clave": settings.BAR_CLAVE}
    )
    assert respuesta.status_code == 302, "No se pudo iniciar sesión en la prueba"
