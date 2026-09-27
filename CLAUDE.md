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
- `ventas/`: Cuenta, ItemConsumo (cuentas abiertas por mesa/cliente)
- `finanzas/`: Gasto, CierreCaja
- `acceso/`: inicio de sesión de un solo usuario (sin modelos). Usuario y
  clave fijos en settings.py; `RequiereSesionMiddleware` protege todo salvo
  /login/ y /static/.

## Interfaz
- Identidad "El Laboratorio Bar": colores y brillos en `static/css/tema.css`;
  tamaños táctiles en `static/css/app.css`.
- Colores con significado: cian = acción principal, amarillo = advertencia,
  rojo #ff453a = peligro, verde neón = confirmación (nunca decorativo).
  Magenta/morado solo decoran.

## Reglas
- Dinero en pesos colombianos como enteros (IntegerField), nunca float.
- El stock de un producto se deriva de MovimientoInventario, no se edita a mano.
- ItemConsumo guarda el precio al momento de agregarlo a la cuenta.
- El stock se descuenta al agregar un item a la cuenta, no al cerrarla.
- Zona horaria America/Bogota, idioma es-co.
- La lógica de dinero y stock lleva pruebas (tests.py).
- Cambios pequeños: un módulo por vez, un commit por funcionalidad.

## Comandos
- Servidor: `python manage.py runserver`
- Migraciones: `python manage.py makemigrations` y `python manage.py migrate`
- Pruebas: `python manage.py test`
