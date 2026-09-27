from django import template

register = template.Library()


def stock_de(producto):
    """Usa la anotación de con_stock() si está; si no, consulta la propiedad."""
    stock = getattr(producto, "stock_actual", None)
    return producto.stock if stock is None else stock


@register.filter
def nivel_stock(producto):
    """Única regla de alerta de stock: "sin" (≤ 0), "bajo" (≤ mínimo) u "ok"."""
    stock = stock_de(producto)
    if stock <= 0:
        return "sin"
    if stock <= producto.stock_minimo:
        return "bajo"
    return "ok"


@register.filter
def stock_actual(producto):
    return stock_de(producto)
