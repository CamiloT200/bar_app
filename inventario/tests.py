from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from .models import Categoria, Compra, DetalleCompra, MovimientoInventario, Producto, Proveedor
from .servicios import LineaCompra, registrar_compra, registrar_movimiento_manual

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
