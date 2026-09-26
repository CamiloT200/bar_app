# Bar App

Aplicación de escritorio-local (web en localhost) para llevar inventario,
ventas, compras, gastos y cierre de caja de un bar familiar.

## Contexto
- Un solo usuario, un solo computador (Windows), sin acceso remoto.
- Base de datos SQLite local. Sin nube por ahora.
- Idioma de la interfaz y del código de dominio: español.

## Stack
- Python + Django, plantillas de Django + Bootstrap.
- Sin React ni frameworks JS pesados.

## Estructura
- `inventario/`: Producto, Categoria, Proveedor, Compra, DetalleCompra, MovimientoInventario
- `ventas/`: Venta, DetalleVenta
- `finanzas/`: Gasto, CierreCaja

## Reglas
- Dinero en pesos colombianos como enteros (IntegerField), nunca float.
- El stock de un producto se deriva de MovimientoInventario, no se edita a mano.
- DetalleVenta guarda el precio al momento de la venta.
- Zona horaria America/Bogota, idioma es-co.
- La lógica de dinero y stock lleva pruebas (tests.py).
- Cambios pequeños: un módulo por vez, un commit por funcionalidad.

## Comandos
- Servidor: `python manage.py runserver`
- Migraciones: `python manage.py makemigrations` y `python manage.py migrate`
- Pruebas: `python manage.py test`
