from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from inventario.models import Categoria, Compra, MovimientoInventario, Producto
from inventario.servicios import LineaCompra, registrar_compra, registrar_movimiento_manual
from ventas.models import Cuenta
from ventas.servicios import abrir_cuenta, agregar_item, cerrar_cuenta, quitar_item

from .models import CierreCaja, Gasto
from .servicios import abrir_caja, cerrar_caja, registrar_gasto

Pago = Cuenta.MetodoPago
Estado = Cuenta.Estado
CatGasto = Gasto.Categoria

T0 = timezone.make_aware(datetime(2025, 3, 14, 18, 0))


def h(horas):
    """Momento T0 + `horas` (puede ser negativo)."""
    return T0 + timedelta(hours=horas)


@contextmanager
def reloj(momento):
    """Fija timezone.now() para los servicios, que lo llaman como timezone.now()."""
    with mock.patch("django.utils.timezone.now", return_value=momento):
        yield


class BaseFinanzas(TestCase):
    def setUp(self):
        cervezas = Categoria.objects.create(nombre="Cervezas")
        self.aguila = Producto.objects.create(nombre="Águila", categoria=cervezas, precio_venta=4000)
        self.club = Producto.objects.create(
            nombre="Club Colombia", categoria=cervezas, precio_venta=5000
        )

    def abrir(self, momento, base=0):
        with reloj(momento):
            return abrir_caja(base)

    def cerrar(self, momento, contado=0):
        with reloj(momento):
            return cerrar_caja(contado)

    def cuenta_cerrada(self, cerrada_en, total, metodo_pago, abierta_en=None):
        """Cuenta ya cobrada, creada por ORM para controlar sus fechas."""
        return Cuenta.objects.create(
            identificador=f"Cuenta {Cuenta.objects.count()}",
            abierta_en=abierta_en or cerrada_en - timedelta(hours=1),
            cerrada_en=cerrada_en,
            estado=Estado.CERRADA,
            total=total,
            metodo_pago=metodo_pago,
        )

    def gasto(self, fecha, monto, pagado_con_caja=True):
        return Gasto.objects.create(
            fecha=fecha,
            categoria=CatGasto.OTRO,
            descripcion="gasto",
            monto=monto,
            pagado_con_caja=pagado_con_caja,
        )

    def compra(self, fecha, total, pagada_con_caja=True):
        return Compra.objects.create(fecha=fecha, total=total, pagada_con_caja=pagada_con_caja)


class AperturaTests(BaseFinanzas):
    # 1
    def test_abrir_caja_y_segunda_apertura_falla(self):
        caja = self.abrir(h(0), base=50000)
        self.assertEqual(caja.base_inicial, 50000)
        self.assertEqual(caja.abierto_en, h(0))
        self.assertTrue(caja.esta_abierta)

        with reloj(h(1)), self.assertRaises(ValidationError):
            abrir_caja(10000)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CierreCaja.objects.create(abierto_en=h(1), base_inicial=0)
        self.assertEqual(CierreCaja.objects.count(), 1)

    # 2
    def test_ventanas_continuas(self):
        primera = self.abrir(h(0))
        self.assertEqual(primera.abierto_en, h(0))
        self.cerrar(h(8))
        segunda = self.abrir(h(20))
        self.assertEqual(segunda.abierto_en, h(8))

    # 3
    def test_restricciones_de_coherencia_en_bd(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CierreCaja.objects.create(abierto_en=h(0), cerrado_en=h(8), base_inicial=0)

        completos = dict(
            ventas_efectivo=0,
            ventas_otros_medios=0,
            compras_efectivo=0,
            gastos_efectivo=0,
            efectivo_esperado=0,
            efectivo_contado=0,
            diferencia=0,
            cuentas_abiertas_al_cerrar=0,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            CierreCaja.objects.create(abierto_en=h(8), cerrado_en=h(8), base_inicial=0, **completos)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CierreCaja.objects.create(abierto_en=h(0), base_inicial=0, ventas_efectivo=100)


class CalculoCierreTests(BaseFinanzas):
    # 4
    def test_ventas_efectivo_solo_cuentas_cerradas_en_efectivo_de_la_ventana(self):
        self.cuenta_cerrada(h(-1), 7000, Pago.EFECTIVO)  # antes de la ventana
        self.abrir(h(0))
        self.cuenta_cerrada(h(2), 10000, Pago.EFECTIVO)  # entra
        self.cuenta_cerrada(h(3), 20000, Pago.NEQUI)  # otro medio
        Cuenta.objects.create(identificador="Mesa abierta")  # sigue abierta
        cierre = self.cerrar(h(8))
        self.assertEqual(cierre.ventas_efectivo, 10000)

    # 5
    def test_ventas_otros_medios(self):
        self.abrir(h(0))
        self.cuenta_cerrada(h(1), 20000, Pago.NEQUI)
        self.cuenta_cerrada(h(2), 5000, Pago.DAVIPLATA)
        self.cuenta_cerrada(h(3), 3000, Pago.TARJETA)
        self.cuenta_cerrada(h(4), 2000, Pago.TRANSFERENCIA)
        self.cuenta_cerrada(h(5), 9000, Pago.EFECTIVO)
        self.cuenta_cerrada(h(6), 0, None)  # mesa que se fue sin consumir
        cierre = self.cerrar(h(8))
        self.assertEqual(cierre.ventas_otros_medios, 30000)
        self.assertEqual(cierre.ventas_efectivo, 9000)

    # 6
    def test_cuenta_abierta_antes_y_cobrada_dentro_entra(self):
        self.abrir(h(0))
        self.cuenta_cerrada(h(2), 15000, Pago.EFECTIVO, abierta_en=h(-30))
        cierre = self.cerrar(h(8))
        self.assertEqual(cierre.ventas_efectivo, 15000)

    # 7
    def test_compras_y_gastos_solo_pagados_con_caja(self):
        self.abrir(h(0))
        self.compra(h(1), 40000)
        self.compra(h(1), 99999, pagada_con_caja=False)
        self.gasto(h(2), 12000)
        self.gasto(h(2), 5000, pagado_con_caja=False)
        self.compra(h(9), 1000)  # después del cierre
        cierre = self.cerrar(h(8))
        self.assertEqual(cierre.compras_efectivo, 40000)
        self.assertEqual(cierre.gastos_efectivo, 12000)

    # 8
    def test_limites_de_la_ventana_semiabierta(self):
        self.abrir(h(0))
        self.gasto(h(0), 1000)  # == abierto_en: entra
        self.gasto(h(8), 2000)  # == cerrado_en: queda para la siguiente
        primera = self.cerrar(h(8))
        self.assertEqual(primera.gastos_efectivo, 1000)

        self.abrir(h(10))
        segunda = self.cerrar(h(12))
        self.assertEqual(segunda.gastos_efectivo, 2000)

    # 9
    def test_esperado_y_diferencia_faltante_sobrante_exacto(self):
        self.abrir(h(0), base=50000)
        self.cuenta_cerrada(h(1), 100000, Pago.EFECTIVO)
        self.compra(h(2), 30000)
        self.gasto(h(3), 10000)
        faltante = self.cerrar(h(8), contado=105000)
        self.assertEqual(faltante.efectivo_esperado, 110000)
        self.assertEqual(faltante.diferencia, -5000)

        self.abrir(h(10), base=20000)
        sobrante = self.cerrar(h(12), contado=21000)
        self.assertEqual(sobrante.efectivo_esperado, 20000)
        self.assertEqual(sobrante.diferencia, 1000)

        self.abrir(h(14), base=0)
        exacto = self.cerrar(h(16), contado=0)
        self.assertEqual(exacto.diferencia, 0)

    # 10
    def test_sin_movimientos_el_esperado_es_la_base(self):
        self.abrir(h(0), base=30000)
        cierre = self.cerrar(h(8), contado=30000)
        self.assertEqual(
            (
                cierre.ventas_efectivo,
                cierre.ventas_otros_medios,
                cierre.compras_efectivo,
                cierre.gastos_efectivo,
            ),
            (0, 0, 0, 0),
        )
        self.assertEqual(cierre.efectivo_esperado, 30000)
        self.assertEqual(cierre.diferencia, 0)
        self.assertEqual(cierre.advertencia, "")

    # 11
    def test_cuentas_abiertas_no_bloquean_el_cierre(self):
        Cuenta.objects.create(identificador="Don Jorge", abierta_en=h(-72))
        self.abrir(h(0))
        Cuenta.objects.create(identificador="Mesa 3", abierta_en=h(1))
        self.cuenta_cerrada(h(2), 8000, Pago.EFECTIVO)
        cierre = self.cerrar(h(8))
        self.assertFalse(cierre.esta_abierta)
        self.assertEqual(cierre.cuentas_abiertas_al_cerrar, 2)
        self.assertIn("2 cuenta(s) abierta(s)", cierre.advertencia)
        self.assertEqual(cierre.ventas_efectivo, 8000)

    # 12
    def test_cerrar_sin_caja_abierta_o_dos_veces_falla(self):
        with reloj(h(1)), self.assertRaises(ValidationError):
            cerrar_caja(0)
        self.abrir(h(0))
        self.cerrar(h(8))
        with reloj(h(9)), self.assertRaises(ValidationError):
            cerrar_caja(0)

    # 13
    def test_las_cifras_del_cierre_quedan_fijas(self):
        self.abrir(h(0))
        gasto = self.gasto(h(1), 12000)
        cierre = self.cerrar(h(8))
        Gasto.objects.filter(pk=gasto.pk).update(monto=99000)
        cierre.refresh_from_db()
        self.assertEqual(cierre.gastos_efectivo, 12000)


class PeriodoCerradoTests(BaseFinanzas):
    def setUp(self):
        super().setUp()
        self.abrir(h(0))
        self.cerrar(h(8))

    # 14
    def test_gasto_en_jornada_cerrada_se_rechaza(self):
        with self.assertRaises(ValidationError):
            registrar_gasto(CatGasto.SERVICIOS, "Hielo", 8000, fecha=h(5))
        self.assertEqual(Gasto.objects.count(), 0)

        registrar_gasto(CatGasto.SERVICIOS, "Hielo", 8000, fecha=h(8))  # justo en el cierre
        registrar_gasto(CatGasto.SERVICIOS, "Hielo", 8000, fecha=h(9))
        self.assertEqual(Gasto.objects.count(), 2)

    # 15
    def test_compra_en_jornada_cerrada_se_rechaza(self):
        with self.assertRaises(ValidationError):
            registrar_compra([LineaCompra(self.aguila, 24, 2500)], fecha=h(3))
        self.assertEqual(Compra.objects.count(), 0)
        self.assertEqual(MovimientoInventario.objects.count(), 0)

        registrar_compra([LineaCompra(self.aguila, 24, 2500)], fecha=h(9))
        self.assertEqual(self.aguila.stock, 24)

    # 16
    def test_reloj_atrasado_bloquea_operaciones_de_ventas(self):
        registrar_movimiento_manual(self.aguila, MovimientoInventario.Tipo.INICIAL, 10)
        with reloj(h(9)):
            mesa = abrir_cuenta("Mesa 1")
            item = agregar_item(mesa, self.aguila, 2)
        movimientos = MovimientoInventario.objects.count()

        with reloj(h(5)):  # el reloj de Windows se atrasó a una jornada cerrada
            with self.assertRaises(ValidationError):
                agregar_item(mesa, self.aguila, 1)
            with self.assertRaises(ValidationError):
                quitar_item(item)
            with self.assertRaises(ValidationError):
                cerrar_cuenta(mesa, Pago.EFECTIVO)

        self.assertEqual(MovimientoInventario.objects.count(), movimientos)
        self.assertEqual(self.aguila.stock, 8)
        mesa.refresh_from_db()
        self.assertTrue(mesa.esta_abierta)

        with reloj(h(10)):
            cerrar_cuenta(mesa, Pago.EFECTIVO)

    # 17
    def test_gasto_sin_caja_abierta_entra_en_la_siguiente(self):
        registrar_gasto(CatGasto.MANTENIMIENTO, "Bombillo", 7000, fecha=h(9))
        self.abrir(h(20))
        cierre = self.cerrar(h(24))
        self.assertEqual(cierre.abierto_en, h(8))
        self.assertEqual(cierre.gastos_efectivo, 7000)


class GastoTests(BaseFinanzas):
    # 18
    def test_validaciones_de_gasto(self):
        invalidos = [
            (CatGasto.OTRO, "Algo", 0),
            (CatGasto.OTRO, "Algo", -500),
            (CatGasto.OTRO, "   ", 1000),
            ("COMIDA", "Algo", 1000),
        ]
        for categoria, descripcion, monto in invalidos:
            with self.subTest(categoria=categoria, descripcion=descripcion, monto=monto):
                with self.assertRaises(ValidationError):
                    registrar_gasto(categoria, descripcion, monto)
        self.assertEqual(Gasto.objects.count(), 0)


class NocheCompletaTests(BaseFinanzas):
    # 19
    def test_noche_completa(self):
        registrar_movimiento_manual(self.aguila, MovimientoInventario.Tipo.INICIAL, 20)
        registrar_movimiento_manual(self.club, MovimientoInventario.Tipo.INICIAL, 10)
        self.abrir(h(0), base=50000)

        with reloj(h(1)):
            mesa1 = abrir_cuenta("Mesa 1")
            agregar_item(mesa1, self.aguila, 5)  # 20.000
            mesa2 = abrir_cuenta("Mesa 2")
            agregar_item(mesa2, self.club, 2)  # 10.000
            mesa3 = abrir_cuenta("Mesa 3")
            agregar_item(mesa3, self.aguila, 1)  # queda abierta
        with reloj(h(2)):
            cerrar_cuenta(mesa1, Pago.EFECTIVO)
            cerrar_cuenta(mesa2, Pago.NEQUI)
        with reloj(h(3)):
            registrar_compra([LineaCompra(self.aguila, 12, 2500)])  # 30.000 de la caja
            registrar_gasto(CatGasto.SERVICIOS, "Hielo", 8000)

        with reloj(h(8)):
            cierre = cerrar_caja(31000, observaciones="  Noche normal  ")

        self.assertEqual(cierre.abierto_en, h(0))
        self.assertEqual(cierre.cerrado_en, h(8))
        self.assertEqual(cierre.base_inicial, 50000)
        self.assertEqual(cierre.ventas_efectivo, 20000)
        self.assertEqual(cierre.ventas_otros_medios, 10000)
        self.assertEqual(cierre.compras_efectivo, 30000)
        self.assertEqual(cierre.gastos_efectivo, 8000)
        self.assertEqual(cierre.efectivo_esperado, 32000)
        self.assertEqual(cierre.efectivo_contado, 31000)
        self.assertEqual(cierre.diferencia, -1000)
        self.assertEqual(cierre.cuentas_abiertas_al_cerrar, 1)
        self.assertIn("1 cuenta(s) abierta(s)", cierre.advertencia)
        self.assertEqual(cierre.observaciones, "Noche normal")
        self.assertEqual(self.aguila.stock, 20 - 5 - 1 + 12)
