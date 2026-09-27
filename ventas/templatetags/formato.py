from django import template

register = template.Library()


@register.filter
def pesos(valor):
    """45000 -> "$ 45.000". Negativos: "-$ 1.000". Vacío si no es un entero."""
    if isinstance(valor, bool) or not isinstance(valor, int):
        return ""
    signo = "-" if valor < 0 else ""
    return f"{signo}$ {abs(valor):,}".replace(",", ".")
