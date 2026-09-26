from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from inventario.models import Categoria, MovimientoInventario, Producto
from inventario.servicios import registrar_movimiento_manual

from .models import Cuenta, ItemConsumo
from .servicios import abrir_cuenta, agregar_item, cerrar_cuenta, quitar_item

Tipo = MovimientoInventario.Tipo
Estado = Cuenta.Estado
Pago = Cuenta.MetodoPago


class BaseVentas(TestCase):
    def setUp(self):
        cervezas = Categoria.objects.create(nombre="Cervezas")
        self.aguila = Producto.objects.create(nombre="Águila", categoria=cervezas, precio_venta=4000)
        self.club = Producto.objects.create(
            nombre="Club Colombia", categoria=cervezas, precio_venta=5000
        )
        self.mesa = abrir_cuenta("Mesa 3")

    def cargar_stock(self, producto, cantidad):
        registrar_movimiento_manual(producto, Tipo.INICIAL, cantidad)

    def assertSinCambiosDeVenta(self, movimientos_antes):
        self.assertEqual(ItemConsumo.objects.count(), 0)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos_antes)


class AgregarItemTests(BaseVentas):
    # 1
    def test_agregar_con_stock_suficiente(self):
        self.cargar_stock(self.aguila, 10)
        item = agregar_item(self.mesa, self.aguila, 3)
        self.assertEqual(item.precio_unitario, 4000)
        self.assertEqual(item.movimiento.tipo, Tipo.VENTA)
        self.assertEqual(item.movimiento.cantidad, -3)
        self.assertIsNone(item.movimiento_ajuste)
        self.assertEqual(self.aguila.stock, 7)

    # 2
    def test_agregar_con_stock_justo_deja_cero(self):
        self.cargar_stock(self.aguila, 4)
        agregar_item(self.mesa, self.aguila, 4)
        self.assertEqual(self.aguila.stock, 0)

    # 3
    def test_stock_insuficiente_sin_forzar_se_rechaza_sin_dejar_nada(self):
        self.cargar_stock(self.aguila, 2)
        antes = MovimientoInventario.objects.count()
        with self.assertRaises(ValidationError):
            agregar_item(self.mesa, self.aguila, 3)
        self.assertSinCambiosDeVenta(antes)
        self.assertEqual(self.aguila.stock, 2)

    # 4
    def test_cambiar_precio_despues_no_altera_el_item(self):
        self.cargar_stock(self.aguila, 5)
        item = agregar_item(self.mesa, self.aguila, 2)
        self.aguila.precio_venta = 4500
        self.aguila.save()
        item.refresh_from_db()
        self.assertEqual(item.precio_unitario, 4000)
        self.assertEqual(item.subtotal, 8000)

    # 5
    def test_no_se_puede_agregar_a_cuenta_cerrada(self):
        self.cargar_stock(self.aguila, 5)
        cerrar_cuenta(self.mesa)
        antes = MovimientoInventario.objects.count()
        with self.assertRaises(ValidationError):
            agregar_item(self.mesa, self.aguila, 1)
        self.assertSinCambiosDeVenta(antes)

    # 6
    def test_mismo_producto_dos_veces_crea_dos_items(self):
        self.cargar_stock(self.aguila, 10)
        agregar_item(self.mesa, self.aguila, 2)
        agregar_item(self.mesa, self.aguila, 3)
        self.assertEqual(self.mesa.items.count(), 2)
        self.assertEqual(self.aguila.stock, 5)

    # 7
    def test_identificador_unico_entre_cuentas_abiertas(self):
        with self.assertRaises(ValidationError):
            abrir_cuenta("Mesa 3")
        with self.assertRaises(ValidationError):
            abrir_cuenta("  mesa 3 ")
        # La restricción de la base de datos también lo impide.
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cuenta.objects.create(identificador="MESA 3")

        cerrar_cuenta(self.mesa)
        otra = abrir_cuenta("Mesa 3")
        self.assertTrue(otra.esta_abierta)
        self.assertNotEqual(otra.pk, self.mesa.pk)


class QuitarItemTests(BaseVentas):
    # 8
    def test_quitar_devuelve_stock_y_crea_reversion(self):
        self.cargar_stock(self.aguila, 10)
        item = agregar_item(self.mesa, self.aguila, 4)
        item = quitar_item(item)
        self.assertIsNotNone(item.retirado_en)
        self.assertFalse(item.vigente)
        self.assertEqual(item.movimiento_reversion.tipo, Tipo.REVERSION_CONSUMO)
        self.assertEqual(item.movimiento_reversion.cantidad, 4)
        self.assertEqual(self.aguila.stock, 10)
        # El item y su movimiento de venta se conservan para auditoría.
        self.assertTrue(ItemConsumo.objects.filter(pk=item.pk).exists())
        self.assertEqual(item.movimiento.cantidad, -4)

    # 9
    def test_item_retirado_no_suma_al_total(self):
        self.cargar_stock(self.aguila, 10)
        agregar_item(self.mesa, self.aguila, 2)
        retirado = agregar_item(self.mesa, self.aguila, 3)
        quitar_item(retirado)
        self.assertEqual(self.mesa.calcular_total(), 2 * 4000)

    # 10
    def test_no_se_puede_quitar_dos_veces_ni_de_cuenta_cerrada(self):
        self.cargar_stock(self.aguila, 10)
        item = agregar_item(self.mesa, self.aguila, 1)
        quitar_item(item)
        with self.assertRaises(ValidationError):
            quitar_item(item)

        otra = abrir_cuenta("Mesa 5")
        item_otra = agregar_item(otra, self.aguila, 1)
        cerrar_cuenta(otra, Pago.EFECTIVO)
        antes = MovimientoInventario.objects.count()
        with self.assertRaises(ValidationError):
            quitar_item(item_otra)
        self.assertEqual(MovimientoInventario.objects.count(), antes)
        self.assertEqual(self.aguila.stock, 9)


class CerrarCuentaTests(BaseVentas):
    # 11
    def test_cerrar_calcula_total_y_fija_campos(self):
        self.cargar_stock(self.aguila, 10)
        self.cargar_stock(self.club, 10)
        agregar_item(self.mesa, self.aguila, 3)  # 3 x 4000
        self.club.precio_venta = 5500
        self.club.save()
        agregar_item(self.mesa, self.club, 2)  # 2 x 5500
        retirado = agregar_item(self.mesa, self.aguila, 5)
        quitar_item(retirado)

        cuenta = cerrar_cuenta(self.mesa, Pago.NEQUI)
        self.assertEqual(cuenta.total, 3 * 4000 + 2 * 5500)
        self.assertEqual(cuenta.metodo_pago, Pago.NEQUI)
        self.assertEqual(cuenta.estado, Estado.CERRADA)
        self.assertIsNotNone(cuenta.cerrada_en)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.total, 23000)

    # 12
    def test_cerrar_no_crea_movimientos_de_inventario(self):
        self.cargar_stock(self.aguila, 10)
        agregar_item(self.mesa, self.aguila, 3)
        antes = MovimientoInventario.objects.count()
        cerrar_cuenta(self.mesa, Pago.EFECTIVO)
        self.assertEqual(MovimientoInventario.objects.count(), antes)
        self.assertEqual(self.aguila.stock, 7)

    # 13
    def test_cerrar_sin_metodo_con_total_o_dos_veces_falla(self):
        self.cargar_stock(self.aguila, 10)
        agregar_item(self.mesa, self.aguila, 1)
        with self.assertRaises(ValidationError):
            cerrar_cuenta(self.mesa)
        self.mesa.refresh_from_db()
        self.assertEqual(self.mesa.estado, Estado.ABIERTA)
        self.assertIsNone(self.mesa.total)

        cerrar_cuenta(self.mesa, Pago.EFECTIVO)
        with self.assertRaises(ValidationError):
            cerrar_cuenta(self.mesa, Pago.TARJETA)
        self.mesa.refresh_from_db()
        self.assertEqual(self.mesa.metodo_pago, Pago.EFECTIVO)


class VenderDeTodosModosTests(BaseVentas):
    # 14
    def test_forzar_sin_nota_se_rechaza_sin_dejar_nada(self):
        antes = MovimientoInventario.objects.count()
        for nota in ["", "   "]:
            with self.subTest(nota=nota), self.assertRaises(ValidationError):
                agregar_item(self.mesa, self.aguila, 2, forzar=True, nota_forzado=nota)
        self.assertSinCambiosDeVenta(antes)

    # 15
    def test_forzar_con_stock_cero_crea_ajuste_y_deja_cero(self):
        item = agregar_item(
            self.mesa, self.aguila, 2, forzar=True, nota_forzado="Había en la nevera"
        )
        ajuste = item.movimiento_ajuste
        self.assertEqual(ajuste.tipo, Tipo.AJUSTE)
        self.assertEqual(ajuste.cantidad, 2)
        self.assertEqual(ajuste.nota, "Había en la nevera")
        self.assertEqual(item.movimiento.cantidad, -2)
        self.assertEqual(self.aguila.stock, 0)

    # 16
    def test_forzar_ajusta_solo_el_faltante(self):
        self.cargar_stock(self.aguila, 1)
        item = agregar_item(self.mesa, self.aguila, 3, forzar=True, nota_forzado="conteo mal")
        self.assertEqual(item.movimiento_ajuste.cantidad, 2)
        self.assertEqual(self.aguila.stock, 0)

    # 17
    def test_forzar_con_stock_suficiente_no_crea_ajuste(self):
        self.cargar_stock(self.aguila, 5)
        item = agregar_item(self.mesa, self.aguila, 3, forzar=True, nota_forzado="por si acaso")
        self.assertIsNone(item.movimiento_ajuste)
        self.assertFalse(self.aguila.movimientos.filter(tipo=Tipo.AJUSTE).exists())
        self.assertEqual(self.aguila.stock, 2)

    # 18
    def test_falla_al_crear_la_venta_revierte_el_ajuste(self):
        crear_original = MovimientoInventario.objects.create

        def falla_en_la_venta(**kwargs):
            if kwargs.get("tipo") == Tipo.VENTA:
                raise RuntimeError("fallo simulado")
            return crear_original(**kwargs)

        with mock.patch.object(
            MovimientoInventario.objects, "create", side_effect=falla_en_la_venta
        ):
            with self.assertRaises(RuntimeError):
                agregar_item(self.mesa, self.aguila, 2, forzar=True, nota_forzado="x")

        self.assertEqual(MovimientoInventario.objects.count(), 0)
        self.assertEqual(ItemConsumo.objects.count(), 0)
        self.assertEqual(self.aguila.stock, 0)

    # 19
    def test_quitar_item_forzado_conserva_el_ajuste(self):
        item = agregar_item(self.mesa, self.aguila, 2, forzar=True, nota_forzado="nevera")
        quitar_item(item)
        item.refresh_from_db()
        self.assertTrue(MovimientoInventario.objects.filter(pk=item.movimiento_ajuste.pk).exists())
        self.assertEqual(item.movimiento_reversion.cantidad, 2)
        self.assertEqual(self.aguila.stock, item.movimiento_ajuste.cantidad)
        self.assertEqual(self.aguila.stock, 2)


class RestriccionesCuentaTests(BaseVentas):
    """Extra: la base de datos rechaza estados incoherentes aunque se salte el servicio."""

    def test_estados_incoherentes_se_rechazan(self):
        casos = [
            dict(estado=Estado.ABIERTA, total=1000),
            dict(estado=Estado.CERRADA, total=None),
            dict(estado=Estado.CERRADA, total=5000, metodo_pago=None),
        ]
        for i, campos in enumerate(casos):
            with self.subTest(campos=campos):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Cuenta.objects.create(
                        identificador=f"Prueba {i}",
                        cerrada_en=None if campos["estado"] == Estado.ABIERTA else self.mesa.abierta_en,
                        **campos,
                    )
