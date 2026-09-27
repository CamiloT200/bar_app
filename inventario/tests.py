from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Categoria, Compra, DetalleCompra, MovimientoInventario, Producto, Proveedor
from .servicios import (
    LineaCompra,
    ajustar_por_conteo,
    crear_producto,
    registrar_compra,
    registrar_movimiento_manual,
)

Tipo = MovimientoInventario.Tipo


class BaseInventario(TestCase):
    def setUp(self):
        self.cervezas = Categoria.objects.create(nombre="Cervezas")
        self.aguila = Producto.objects.create(
            nombre="Águila", categoria=self.cervezas, precio_venta=4000, stock_minimo=10
        )
        self.club = Producto.objects.create(
            nombre="Club Colombia", categoria=self.cervezas, precio_venta=5000, stock_minimo=5
        )
        self.proveedor = Proveedor.objects.create(nombre="Distribuidora Bavaria")

    def mover(self, producto, tipo, cantidad):
        return MovimientoInventario.objects.create(producto=producto, tipo=tipo, cantidad=cantidad)


class StockTests(BaseInventario):
    def test_producto_sin_movimientos_tiene_stock_cero(self):
        self.assertEqual(self.aguila.stock, 0)

    def test_stock_es_la_suma_con_signo_de_los_movimientos(self):
        self.mover(self.aguila, Tipo.INICIAL, 24)
        self.mover(self.aguila, Tipo.VENTA, -5)
        self.mover(self.aguila, Tipo.MERMA, -1)
        self.mover(self.aguila, Tipo.AJUSTE, 2)
        self.mover(self.club, Tipo.INICIAL, 7)
        self.assertEqual(self.aguila.stock, 20)
        self.assertEqual(self.club.stock, 7)

    def test_con_stock_anota_todos_los_productos_en_una_consulta(self):
        self.mover(self.aguila, Tipo.INICIAL, 3)
        self.mover(self.club, Tipo.INICIAL, 8)
        with self.assertNumQueries(1):
            stocks = {p.nombre: p.stock_actual for p in Producto.objects.con_stock()}
        self.assertEqual(stocks, {"Águila": 3, "Club Colombia": 8})

    def test_con_stock_permite_filtrar_bajo_minimo(self):
        from django.db.models import F

        self.mover(self.aguila, Tipo.INICIAL, 3)  # mínimo 10 -> bajo
        self.mover(self.club, Tipo.INICIAL, 8)  # mínimo 5 -> bien
        bajos = Producto.objects.con_stock().filter(stock_actual__lte=F("stock_minimo"))
        self.assertEqual(list(bajos), [self.aguila])


class RestriccionesMovimientoTests(BaseInventario):
    def assertRechazado(self, tipo, cantidad):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.mover(self.aguila, tipo, cantidad)

    def test_cantidad_cero_se_rechaza(self):
        for tipo in Tipo.values:
            with self.subTest(tipo=tipo):
                self.assertRechazado(tipo, 0)

    def test_entradas_no_pueden_ser_negativas(self):
        for tipo in [Tipo.INICIAL, Tipo.COMPRA, Tipo.REVERSION_CONSUMO]:
            with self.subTest(tipo=tipo):
                self.assertRechazado(tipo, -1)

    def test_salidas_no_pueden_ser_positivas(self):
        for tipo in [Tipo.VENTA, Tipo.MERMA]:
            with self.subTest(tipo=tipo):
                self.assertRechazado(tipo, 1)

    def test_ajuste_acepta_ambos_signos(self):
        self.mover(self.aguila, Tipo.AJUSTE, 5)
        self.mover(self.aguila, Tipo.AJUSTE, -2)
        self.assertEqual(self.aguila.stock, 3)

    def test_no_se_puede_borrar_producto_con_movimientos(self):
        self.mover(self.aguila, Tipo.INICIAL, 1)
        with self.assertRaises(ProtectedError):
            self.aguila.delete()


class RegistrarCompraTests(BaseInventario):
    def test_compra_crea_detalles_movimientos_y_calcula_total(self):
        compra = registrar_compra(
            [LineaCompra(self.aguila, 30, 2500), LineaCompra(self.club, 12, 3200)],
            proveedor=self.proveedor,
            numero_factura="F-001",
        )
        self.assertEqual(compra.total, 30 * 2500 + 12 * 3200)
        self.assertEqual(compra.detalles.count(), 2)
        self.assertEqual(self.aguila.stock, 30)
        self.assertEqual(self.club.stock, 12)
        for detalle in compra.detalles.all():
            self.assertEqual(detalle.movimiento.tipo, Tipo.COMPRA)
            self.assertEqual(detalle.movimiento.cantidad, detalle.cantidad)
            self.assertEqual(detalle.movimiento.producto, detalle.producto)
            self.assertEqual(detalle.movimiento.fecha, compra.fecha)
        self.assertEqual(compra.total, sum(d.subtotal for d in compra.detalles.all()))

    def test_compra_sin_proveedor_es_valida(self):
        compra = registrar_compra([LineaCompra(self.aguila, 6, 2600)])
        self.assertIsNone(compra.proveedor)
        self.assertEqual(self.aguila.stock, 6)

    def test_mismo_producto_en_dos_lineas_suma_ambas(self):
        registrar_compra([LineaCompra(self.aguila, 10, 2500), LineaCompra(self.aguila, 5, 2400)])
        self.assertEqual(self.aguila.stock, 15)

    def test_costo_cero_se_permite_para_bonificaciones(self):
        compra = registrar_compra([LineaCompra(self.aguila, 2, 0)])
        self.assertEqual(compra.total, 0)
        self.assertEqual(self.aguila.stock, 2)

    def assertNadaCreado(self):
        self.assertEqual(Compra.objects.count(), 0)
        self.assertEqual(DetalleCompra.objects.count(), 0)
        self.assertEqual(MovimientoInventario.objects.count(), 0)

    def test_compra_sin_lineas_se_rechaza(self):
        with self.assertRaises(ValidationError):
            registrar_compra([])
        self.assertNadaCreado()

    def test_cantidad_invalida_rechaza_toda_la_compra(self):
        for cantidad in [0, -3]:
            with self.subTest(cantidad=cantidad), self.assertRaises(ValidationError):
                registrar_compra(
                    [LineaCompra(self.aguila, 10, 2500), LineaCompra(self.club, cantidad, 3000)]
                )
        self.assertNadaCreado()

    def test_costo_negativo_rechaza_la_compra(self):
        with self.assertRaises(ValidationError):
            registrar_compra([LineaCompra(self.aguila, 10, -1)])
        self.assertNadaCreado()

    def test_falla_a_mitad_de_compra_no_deja_nada_guardado(self):
        crear_original = DetalleCompra.objects.create
        llamadas = []

        def falla_en_la_segunda(**kwargs):
            llamadas.append(1)
            if len(llamadas) == 2:
                raise RuntimeError("fallo simulado")
            return crear_original(**kwargs)

        with mock.patch.object(DetalleCompra.objects, "create", side_effect=falla_en_la_segunda):
            with self.assertRaises(RuntimeError):
                registrar_compra(
                    [LineaCompra(self.aguila, 10, 2500), LineaCompra(self.club, 5, 3000)]
                )
        self.assertNadaCreado()
        self.assertEqual(self.aguila.stock, 0)


class MovimientoManualTests(BaseInventario):
    def test_inventario_inicial(self):
        registrar_movimiento_manual(self.aguila, Tipo.INICIAL, 24)
        self.assertEqual(self.aguila.stock, 24)

    def test_merma_con_stock_suficiente(self):
        registrar_movimiento_manual(self.aguila, Tipo.INICIAL, 5)
        registrar_movimiento_manual(self.aguila, Tipo.MERMA, -5, nota="Se rompió la canasta")
        self.assertEqual(self.aguila.stock, 0)

    def test_merma_que_dejaria_stock_negativo_se_rechaza(self):
        registrar_movimiento_manual(self.aguila, Tipo.INICIAL, 2)
        with self.assertRaises(ValidationError):
            registrar_movimiento_manual(self.aguila, Tipo.MERMA, -3, nota="rotas")
        self.assertEqual(self.aguila.stock, 2)
        self.assertEqual(self.aguila.movimientos.count(), 1)

    def test_ajuste_negativo_no_puede_dejar_stock_negativo(self):
        registrar_movimiento_manual(self.aguila, Tipo.INICIAL, 2)
        with self.assertRaises(ValidationError):
            registrar_movimiento_manual(self.aguila, Tipo.AJUSTE, -3, nota="conteo")
        registrar_movimiento_manual(self.aguila, Tipo.AJUSTE, -2, nota="conteo")
        self.assertEqual(self.aguila.stock, 0)

    def test_ajuste_y_merma_exigen_nota(self):
        registrar_movimiento_manual(self.aguila, Tipo.INICIAL, 5)
        for tipo, cantidad in [(Tipo.AJUSTE, 1), (Tipo.MERMA, -1)]:
            with self.subTest(tipo=tipo), self.assertRaises(ValidationError):
                registrar_movimiento_manual(self.aguila, tipo, cantidad, nota="   ")
        self.assertEqual(self.aguila.stock, 5)

    def test_signo_equivocado_da_error_de_validacion(self):
        with self.assertRaises(ValidationError):
            registrar_movimiento_manual(self.aguila, Tipo.INICIAL, -1)
        with self.assertRaises(ValidationError):
            registrar_movimiento_manual(self.aguila, Tipo.MERMA, 1, nota="x")

    def test_tipos_de_venta_y_compra_no_son_manuales(self):
        for tipo in [Tipo.VENTA, Tipo.COMPRA, Tipo.REVERSION_CONSUMO]:
            with self.subTest(tipo=tipo), self.assertRaises(ValidationError):
                registrar_movimiento_manual(self.aguila, tipo, 1, nota="x")
        self.assertEqual(MovimientoInventario.objects.count(), 0)


# ---------------------------------------------------------------------------
# Servicios nuevos de la interfaz de inventario
# ---------------------------------------------------------------------------


class AjusteYCreacionTests(BaseInventario):
    # 1
    def test_ajustar_por_conteo_deja_el_stock_en_lo_contado(self):
        registrar_movimiento_manual(self.aguila, Tipo.INICIAL, 24)
        abajo = ajustar_por_conteo(self.aguila, 20, "Conteo físico")
        self.assertEqual((abajo.tipo, abajo.cantidad, abajo.nota), (Tipo.AJUSTE, -4, "Conteo físico"))
        self.assertEqual(self.aguila.stock, 20)

        arriba = ajustar_por_conteo(self.aguila, 25, "Apareció una canasta")
        self.assertEqual(arriba.cantidad, 5)
        self.assertEqual(self.aguila.stock, 25)

    # 2
    def test_ajustar_por_conteo_rechaza_sin_diferencia_y_sin_nota(self):
        registrar_movimiento_manual(self.aguila, Tipo.INICIAL, 10)
        with self.assertRaises(ValidationError) as ctx:
            ajustar_por_conteo(self.aguila, 10, "Conteo físico")
        self.assertEqual(ctx.exception.code, "sin_diferencia")
        with self.assertRaises(ValidationError):
            ajustar_por_conteo(self.aguila, 7, "   ")
        self.assertEqual(self.aguila.movimientos.count(), 1)
        self.assertEqual(self.aguila.stock, 10)

    # 3
    def test_inicial_se_rechaza_si_ya_hay_movimientos(self):
        registrar_movimiento_manual(self.aguila, Tipo.INICIAL, 5)
        with self.assertRaises(ValidationError) as ctx:
            registrar_movimiento_manual(self.aguila, Tipo.INICIAL, 5)
        self.assertEqual(ctx.exception.code, "inicial_repetido")

        registrar_compra([LineaCompra(self.club, 6, 3000)])
        with self.assertRaises(ValidationError):
            registrar_movimiento_manual(self.club, Tipo.INICIAL, 2)
        self.assertEqual((self.aguila.stock, self.club.stock), (5, 6))

    # 4
    def test_crear_producto_con_stock_inicial_es_atomico(self):
        datos = dict(nombre="Poker", categoria=self.cervezas, precio_venta=4000)
        poker = crear_producto(datos, stock_inicial=12)
        self.assertEqual(poker.stock, 12)
        self.assertEqual(poker.movimientos.get().tipo, Tipo.INICIAL)

        sin_stock = crear_producto(dict(datos, nombre="Pilsen"))
        self.assertFalse(sin_stock.movimientos.exists())

        with mock.patch(
            "inventario.servicios.registrar_movimiento_manual", side_effect=RuntimeError("falla")
        ):
            with self.assertRaises(RuntimeError):
                crear_producto(dict(datos, nombre="Costeña"), stock_inicial=6)
        self.assertFalse(Producto.objects.filter(nombre="Costeña").exists())


# ---------------------------------------------------------------------------
# Interfaz web de inventario
# ---------------------------------------------------------------------------


class VistasInventarioTests(BaseInventario):
    def url(self, nombre, *args):
        return reverse(f"inventario:{nombre}", args=args)

    def mensajes(self, respuesta):
        return [str(m) for m in respuesta.context["messages"]]

    def nombres(self, respuesta):
        return [p.nombre for p in respuesta.context["productos"]]

    # 5
    def test_lista_muestra_stock_alertas_y_filtra(self):
        licores = Categoria.objects.create(nombre="Licores")
        aguardiente = Producto.objects.create(
            nombre="Aguardiente", categoria=licores, precio_venta=60000, stock_minimo=2
        )
        Producto.objects.create(nombre="Pilsen", categoria=self.cervezas, precio_venta=3500, activo=False)
        self.mover(self.aguila, Tipo.INICIAL, 3)  # mínimo 10 -> bajo
        self.mover(aguardiente, Tipo.INICIAL, 20)  # ok; Club queda en 0 -> sin

        respuesta = self.client.get(self.url("productos"))
        self.assertEqual(self.nombres(respuesta), ["Aguardiente", "Águila", "Club Colombia"])
        self.assertEqual(respuesta.context["alertas"], 2)
        self.assertContains(respuesta, 'data-nombre="Águila" data-nivel="bajo"')
        self.assertContains(respuesta, 'data-nombre="Club Colombia" data-nivel="sin"')
        self.assertContains(respuesta, 'data-nombre="Aguardiente" data-nivel="ok"')
        self.assertContains(respuesta, "table-warning")
        self.assertContains(respuesta, "table-danger")

        respuesta = self.client.get(self.url("productos"), {"categoria": licores.pk})
        self.assertEqual(self.nombres(respuesta), ["Aguardiente"])
        respuesta = self.client.get(self.url("productos"), {"alerta": "1"})
        self.assertEqual(self.nombres(respuesta), ["Águila", "Club Colombia"])
        respuesta = self.client.get(self.url("productos"), {"inactivos": "1"})
        self.assertIn("Pilsen", self.nombres(respuesta))

    # 6
    def test_crear_producto_con_inventario_inicial_y_nombre_repetido(self):
        datos = {
            "nombre": "Poker",
            "categoria": self.cervezas.pk,
            "precio_venta": 4000,
            "stock_minimo": 6,
            "activo": "on",
            "stock_inicial": 24,
        }
        respuesta = self.client.post(self.url("producto_nuevo"), datos)
        self.assertRedirects(respuesta, self.url("productos"))
        self.assertEqual(Producto.objects.get(nombre="Poker").stock, 24)

        respuesta = self.client.post(self.url("producto_nuevo"), dict(datos, nombre="  aguila "))
        self.assertEqual(respuesta.status_code, 200)
        self.assertFormError(respuesta.context["form"], "nombre", "Ya existe «Águila».")
        self.assertEqual(Producto.objects.count(), 3)

    # 7
    def test_editar_precio_no_altera_items_de_cuenta_abierta(self):
        from ventas.servicios import abrir_cuenta, agregar_item

        registrar_movimiento_manual(self.aguila, Tipo.INICIAL, 10)
        item = agregar_item(abrir_cuenta("Mesa 1"), self.aguila, 2)

        respuesta = self.client.get(self.url("producto_editar", self.aguila.pk))
        self.assertEqual(respuesta.context["stock"], 8)

        datos = {
            "nombre": "Águila",  # el mismo nombre no choca consigo mismo
            "categoria": self.cervezas.pk,
            "precio_venta": 4500,
            "stock_minimo": 10,
            "activo": "on",
        }
        respuesta = self.client.post(self.url("producto_editar", self.aguila.pk), datos)
        self.assertRedirects(respuesta, self.url("productos"))
        self.aguila.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(self.aguila.precio_venta, 4500)
        self.assertEqual(item.precio_unitario, 4000)
        self.assertEqual(self.aguila.stock, 8)

    # 8
    def test_categorias_crear_renombrar_y_borrar(self):
        respuesta = self.client.post(self.url("categoria_crear"), {"nombre": "Licores"}, follow=True)
        self.assertIn("Categoría Licores creada.", self.mensajes(respuesta))
        licores = Categoria.objects.get(nombre="Licores")

        respuesta = self.client.post(self.url("categoria_crear"), {"nombre": "cervezas"}, follow=True)
        self.assertIn("Ya existe «Cervezas».", self.mensajes(respuesta))
        self.assertEqual(Categoria.objects.count(), 2)

        self.client.post(self.url("categoria_renombrar", licores.pk), {"nombre": "Licores y rones"})
        licores.refresh_from_db()
        self.assertEqual(licores.nombre, "Licores y rones")

        respuesta = self.client.post(self.url("categoria_borrar", self.cervezas.pk), follow=True)
        self.assertIn("Cervezas tiene productos: no se puede borrar.", self.mensajes(respuesta))
        self.assertTrue(Categoria.objects.filter(pk=self.cervezas.pk).exists())

        self.client.post(self.url("categoria_borrar", licores.pk))
        self.assertFalse(Categoria.objects.filter(pk=licores.pk).exists())

    def datos_compra(self, lineas, **encabezado):
        datos = {
            "proveedor": "",
            "fecha": "",
            "numero_factura": "F-9",
            "pagada_con_caja": "on",
            "lineas-TOTAL_FORMS": "5",
            "lineas-INITIAL_FORMS": "0",
            "lineas-MIN_NUM_FORMS": "0",
            "lineas-MAX_NUM_FORMS": "1000",
        }
        datos.update(encabezado)
        for i, (producto, cantidad, costo) in enumerate(lineas):
            datos[f"lineas-{i}-producto"] = producto.pk
            datos[f"lineas-{i}-cantidad"] = cantidad
            datos[f"lineas-{i}-costo_unitario"] = costo
        return datos

    # 9
    def test_registrar_compra_desde_el_formulario(self):
        from finanzas.models import CierreCaja

        # Dos líneas llenas y tres vacías.
        datos = self.datos_compra(
            [(self.aguila, 30, 2500), (self.club, 12, 3200)], proveedor=self.proveedor.pk
        )
        respuesta = self.client.post(self.url("compra_nueva"), datos, follow=True)
        compra = Compra.objects.get()
        self.assertRedirects(respuesta, self.url("compra", compra.pk))
        self.assertEqual(compra.total, 30 * 2500 + 12 * 3200)
        self.assertEqual(compra.proveedor, self.proveedor)
        self.assertIn("Compra registrada: $ 113.400 (2 líneas).", self.mensajes(respuesta))
        self.assertEqual((self.aguila.stock, self.club.stock), (30, 12))

        # El formulario propone el último costo de cada producto.
        respuesta = self.client.get(self.url("compra_nueva"))
        self.assertContains(respuesta, f'value="{self.aguila.pk}" data-costo="2500"')

        # Sin líneas.
        respuesta = self.client.post(self.url("compra_nueva"), self.datos_compra([]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "La compra debe tener al menos una línea.")

        # Fecha dentro de una jornada cerrada: error y las líneas se conservan.
        ahora = timezone.now()
        CierreCaja.objects.create(
            abierto_en=ahora - timedelta(days=400),
            cerrado_en=ahora - timedelta(minutes=5),
            base_inicial=0,
            ventas_efectivo=0,
            ventas_otros_medios=0,
            compras_efectivo=0,
            gastos_efectivo=0,
            efectivo_esperado=0,
            efectivo_contado=0,
            diferencia=0,
            cuentas_abiertas_al_cerrar=0,
        )
        datos = self.datos_compra([(self.aguila, 7, 2600)], fecha="2025-06-01T10:00")
        respuesta = self.client.post(self.url("compra_nueva"), datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "jornada ya cerrada")
        self.assertContains(respuesta, 'name="lineas-0-cantidad" value="7"')
        self.assertContains(respuesta, 'name="lineas-0-costo_unitario" value="2600"')
        self.assertEqual(Compra.objects.count(), 1)

    # 10
    def test_movimiento_manual_desde_el_formulario(self):
        url = self.url("producto_movimiento", self.club.pk)
        self.assertContains(self.client.get(url), 'id="tipo-INICIAL"')

        self.client.post(url, {"tipo": "INICIAL", "cantidad": 10})
        self.assertEqual(self.club.stock, 10)
        self.assertNotContains(self.client.get(url), 'id="tipo-INICIAL"')

        respuesta = self.client.post(url, {"tipo": "MERMA", "cantidad": 2, "nota": "Se rompió"}, follow=True)
        self.assertIn("Club Colombia: se registró merma de 2 (stock 8).", self.mensajes(respuesta))
        self.assertEqual(self.club.movimientos.first().cantidad, -2)

        self.client.post(url, {"tipo": "AJUSTE", "cantidad": 5, "nota": "Conteo físico"})
        self.assertEqual(self.club.stock, 5)

        respuesta = self.client.post(url, {"tipo": "MERMA", "cantidad": 1, "nota": ""})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "requieren una nota")

        respuesta = self.client.post(url, {"tipo": "MERMA", "cantidad": 9, "nota": "rotas"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Stock insuficiente")

        # Intentar INICIAL a mano cuando ya no se ofrece.
        respuesta = self.client.post(url, {"tipo": "INICIAL", "cantidad": 3})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.club.stock, 5)

    # 11
    def test_rutas_que_modifican_no_aceptan_get(self):
        for url in [
            self.url("categoria_crear"),
            self.url("categoria_renombrar", self.cervezas.pk),
            self.url("categoria_borrar", self.cervezas.pk),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)
        self.assertTrue(Categoria.objects.filter(pk=self.cervezas.pk).exists())
