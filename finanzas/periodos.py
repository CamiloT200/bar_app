"""Regla de periodos cerrados, compartida por inventario, ventas y finanzas.

Solo importa finanzas.models (que no importa otras apps), para que los
servicios de inventario y ventas puedan usarla sin dependencias circulares.
"""
from django.core.exceptions import ValidationError
from django.db.models import Max
from django.utils import timezone

from .models import CierreCaja


def verificar_periodo_abierto(fecha):
    """Rechaza fechas que caen dentro de una jornada ya cerrada.

    Como las ventanas son continuas, basta comparar con el último cerrado_en.
    """
    ultimo_cierre = CierreCaja.objects.aggregate(ultimo=Max("cerrado_en"))["ultimo"]
    if ultimo_cierre is not None and fecha < ultimo_cierre:
        raise ValidationError(
            "Esa fecha pertenece a una jornada ya cerrada "
            f"(último cierre: {timezone.localtime(ultimo_cierre):%Y-%m-%d %H:%M}); "
            "regístralo con fecha de hoy."
        )
