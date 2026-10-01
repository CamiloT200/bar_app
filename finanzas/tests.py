from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from acceso.pruebas import iniciar_sesion
from inventario.models import Categoria, Compra, MovimientoInventario, Producto
from inventario.servicios import LineaCompra, registrar_compra, registrar_movimiento_manual
from ventas.models import Cuenta, PagoCuenta
from ventas.servicios import abrir_cuenta, agregar_item, cerrar_cuenta, quitar_item

from .models import CierreCaja, Gasto
from .models import ConteoMedioPago
from .servicios import (
    MEDIOS_A_CONTAR,
    abrir_caja,
    caja_abierta,
    cerrar_caja,
    registrar_gasto,
    resumen_ventana,
)

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
        """Cuenta ya cobrada, creada por ORM para controlar sus fechas.

        Como cerrar_cuenta, deja su PagoCuenta (lo que suma la caja).
        """
        cuenta = Cuenta.objects.create(
            identificador=f"Cuenta {Cuenta.objects.count()}",
            abierta_en=abierta_en or cerrada_en - timedelta(hours=1),
            cerrada_en=cerrada_en,
            estado=Estado.CERRADA,
            total=total,
            metodo_pago=metodo_pago,
        )
        if total:
            PagoCuenta.objects.create(cuenta=cuenta, metodo=metodo_pago, monto=total)
        return cuenta

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


# ---------------------------------------------------------------------------
# Interfaz web de finanzas
# ---------------------------------------------------------------------------


class BaseJornada(BaseFinanzas):
    def cargar_jornada(self):
        self.abrir(h(0), base=50000)
        self.cuenta_cerrada(h(1), 20000, Pago.EFECTIVO)
        self.cuenta_cerrada(h(2), 15000, Pago.NEQUI)
        self.compra(h(3), 30000)
        self.gasto(h(4), 8000)
        self.gasto(h(4), 5000, pagado_con_caja=False)


class ResumenVentanaTests(BaseJornada):
    # 1
    def test_resumen_en_vivo_coincide_con_el_cierre(self):
        self.cargar_jornada()
        Cuenta.objects.create(identificador="Mesa 9")
        resumen = resumen_ventana(h(0), h(8))
        cierre = self.cerrar(h(8), contado=30000)
        self.assertEqual(
            (
                resumen.ventas_efectivo,
                resumen.ventas_otros_medios,
                resumen.compras_efectivo,
                resumen.gastos_efectivo,
                resumen.efectivo_esperado(cierre.base_inicial),
                resumen.cuentas_abiertas,
            ),
            (
                cierre.ventas_efectivo,
                cierre.ventas_otros_medios,
                cierre.compras_efectivo,
                cierre.gastos_efectivo,
                cierre.efectivo_esperado,
                cierre.cuentas_abiertas_al_cerrar,
            ),
        )
        self.assertEqual(cierre.efectivo_esperado, 32000)


class VistasFinanzasTests(BaseJornada):
    def setUp(self):
        super().setUp()
        iniciar_sesion(self.client)

    def url(self, nombre, *args):
        return reverse(f"finanzas:{nombre}", args=args)

    def mensajes(self, respuesta):
        return [str(m) for m in respuesta.context["messages"]]

    def medios_en_cero(self):
        return {f"contado_{m}": "0" for m in MEDIOS_A_CONTAR}

    # 2
    def test_caja_sin_caja_abierta_y_pendiente(self):
        respuesta = self.client.get(self.url("caja"))
        self.assertContains(respuesta, "Aún no se ha abierto ninguna caja.")
        self.assertContains(respuesta, 'name="base_inicial"')
        self.assertIsNone(respuesta.context["resumen"])

        self.abrir(h(0))
        self.cerrar(h(8))
        self.gasto(h(9), 7000)
        respuesta = self.client.get(self.url("caja"))
        self.assertIsNone(respuesta.context["caja"])
        self.assertEqual(respuesta.context["desde"], h(8))
        self.assertEqual(respuesta.context["resumen"].gastos_efectivo, 7000)
        self.assertContains(respuesta, "Pendiente para la próxima jornada")

    # 3
    def test_abrir_caja(self):
        respuesta = self.client.post(self.url("abrir"), {"base_inicial": "50000", "siguiente": "/ventas/"})
        self.assertRedirects(respuesta, "/ventas/", fetch_redirect_response=False)
        self.assertEqual(caja_abierta().base_inicial, 50000)

        respuesta = self.client.post(self.url("abrir"), {"base_inicial": "1000"}, follow=True)
        self.assertIn("Ya hay una caja abierta; ciérrala antes de abrir otra.", self.mensajes(respuesta))

        cerrar_caja(0)
        for base in ["-5", ""]:
            with self.subTest(base=base):
                respuesta = self.client.post(self.url("abrir"), {"base_inicial": base}, follow=True)
                self.assertIn(
                    "Escribe la base inicial (un número de 0 en adelante).", self.mensajes(respuesta)
                )
        self.assertIsNone(caja_abierta())

        respuesta = self.client.post(
            self.url("abrir"),
            {"base_inicial": "1000", "siguiente": "https://malicioso.example.com/"},
            follow=True,
        )
        self.assertRedirects(respuesta, self.url("caja"))
        self.assertIn("Caja abierta con base $ 1.000.", self.mensajes(respuesta))

    # 4
    def test_caja_abierta_muestra_resumen_y_cuentas_abiertas(self):
        self.cargar_jornada()
        Cuenta.objects.create(identificador="Mesa 3")
        Cuenta.objects.create(identificador="Don Jorge")

        respuesta = self.client.get(self.url("caja"))
        resumen = respuesta.context["resumen"]
        self.assertEqual(
            (resumen.ventas_efectivo, resumen.ventas_otros_medios,
             resumen.compras_efectivo, resumen.gastos_efectivo),
            (20000, 15000, 30000, 8000),
        )
        self.assertEqual(respuesta.context["esperado"], 32000)
        self.assertContains(respuesta, "$ 32.000")
        self.assertContains(respuesta, "2 cuentas abiertas")
        self.assertContains(respuesta, "Mesa 3")
        self.assertContains(respuesta, "Don Jorge")
        self.assertContains(respuesta, "Cuenta 0")  # cuenta cobrada en la ventana

    # 5
    def test_cerrar_caja(self):
        respuesta = self.client.get(self.url("cerrar"), follow=True)
        self.assertRedirects(respuesta, self.url("caja"))
        self.assertIn("No hay caja abierta para cerrar.", self.mensajes(respuesta))

        self.abrir(h(0), base=50000)
        self.cuenta_cerrada(h(1), 20000, Pago.EFECTIVO)
        Cuenta.objects.create(identificador="Mesa 9")

        respuesta = self.client.get(self.url("cerrar"))
        self.assertEqual(respuesta.context["esperado"], 70000)
        self.assertContains(respuesta, "Mesa 9")
        self.assertContains(respuesta, 'name="efectivo_contado"')
        cifras = respuesta.context["resumen"].cifras

        respuesta = self.client.post(self.url("cerrar"), {"efectivo_contado": ""})
        self.assertContains(respuesta, "Cuenta el efectivo antes de cerrar.")
        self.assertIsNotNone(caja_abierta())

        respuesta = self.client.post(
            self.url("cerrar"),
            {"efectivo_contado": "67000", "cifras_previas": cifras, "observaciones": "Todo normal",
             **self.medios_en_cero()},
            follow=True,
        )
        cierre = CierreCaja.objects.get(pk=respuesta.context["cierre"].pk)
        self.assertRedirects(respuesta, self.url("cierre", cierre.pk))
        self.assertEqual((cierre.efectivo_contado, cierre.diferencia), (67000, -3000))
        self.assertEqual(cierre.observaciones, "Todo normal")
        self.assertEqual(cierre.cuentas_abiertas_al_cerrar, 1)
        mensajes = self.mensajes(respuesta)
        self.assertIn(
            "Caja cerrada. Efectivo: faltan $ 3.000. Otros medios: cuadra exacto. "
            "Total: faltan $ 3.000.",
            mensajes,
        )
        self.assertFalse(any("cambiaron" in m for m in mensajes))

        # Si las cifras cambiaron entre abrir la pantalla y confirmar, se avisa.
        abrir_caja(0)
        respuesta = self.client.post(
            self.url("cerrar"),
            {"efectivo_contado": "0", "cifras_previas": "1-1-1-1", **self.medios_en_cero()},
            follow=True,
        )
        self.assertTrue(any("cambiaron" in m for m in self.mensajes(respuesta)))

    # 6
    def test_registrar_gasto(self):
        self.abrir(h(0))
        datos = {
            "categoria": CatGasto.SERVICIOS,
            "descripcion": "Hielo",
            "monto": "8000",
            "pagado_con_caja": "on",
            "fecha": "",
        }
        respuesta = self.client.post(self.url("gasto_nuevo"), datos, follow=True)
        self.assertRedirects(respuesta, self.url("caja"))
        self.assertIn("Gasto registrado: $ 8.000 (Servicios: Hielo).", self.mensajes(respuesta))
        self.assertEqual(respuesta.context["resumen"].gastos_efectivo, 8000)

        respuesta = self.client.post(
            self.url("gasto_nuevo"), dict(datos, descripcion="", monto="0")
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context["form"].errors["descripcion"])
        self.assertTrue(respuesta.context["form"].errors["monto"])
        self.assertEqual(Gasto.objects.count(), 1)

        self.cerrar(h(8))
        respuesta = self.client.post(
            self.url("gasto_nuevo"),
            dict(datos, descripcion="Hielo del sábado", fecha="2025-03-14T20:00"),
        )
        self.assertContains(respuesta, "jornada ya cerrada")
        self.assertContains(respuesta, 'value="Hielo del sábado"')
        self.assertEqual(Gasto.objects.count(), 1)

    # 7
    def test_historial_y_detalle_de_cierres(self):
        self.abrir(h(0))
        self.cuenta_cerrada(h(1), 10000, Pago.EFECTIVO)
        primero = self.cerrar(h(8), contado=9000)  # faltan 1.000
        self.abrir(h(10))
        segundo = self.cerrar(h(12), contado=500)  # sobran 500

        respuesta = self.client.get(self.url("cierres"))
        self.assertEqual(list(respuesta.context["pagina"]), [segundo, primero])
        self.assertEqual(respuesta.context["total_diferencias"], -500)
        self.assertContains(respuesta, "text-danger")
        self.assertContains(respuesta, "text-success")

        respuesta = self.client.get(self.url("cierre", primero.pk))
        self.assertContains(respuesta, "Faltaron")
        self.assertContains(respuesta, "$ 1.000")
        self.assertContains(respuesta, "Cuenta 0")
        self.assertEqual(list(respuesta.context["detalle"].cuentas_cobradas), list(Cuenta.objects.all()))

    # 8
    def test_abrir_no_acepta_get(self):
        self.assertEqual(self.client.get(self.url("abrir")).status_code, 405)
        self.assertIsNone(caja_abierta())

    # 9
    def test_tablero_de_ventas_enlaza_a_abrir_caja(self):
        enlace = f'href="{self.url("caja")}?siguiente=/ventas/"'
        respuesta = self.client.get(reverse("ventas:tablero"))
        self.assertContains(respuesta, enlace)

        abrir_caja(50000)
        respuesta = self.client.get(reverse("ventas:tablero"))
        self.assertNotContains(respuesta, 'id="abrir-caja"')
        self.assertNotContains(respuesta, "No hay caja abierta")


class ConteoPorMedioTests(BaseJornada):
    def jornada_con_varios_medios(self):
        self.abrir(h(0), base=50000)
        self.cuenta_cerrada(h(1), 20000, Pago.EFECTIVO)
        self.cuenta_cerrada(h(2), 15000, Pago.NEQUI)
        self.cuenta_cerrada(h(3), 7000, Pago.TARJETA)
        # Una cuenta dividida: cada parte suma en su medio.
        dividida = self.cuenta_cerrada(h(4), 3000, Pago.DAVIPLATA)
        dividida.total, dividida.metodo_pago = 10000, "MIXTO"
        dividida.save()
        PagoCuenta.objects.create(cuenta=dividida, metodo=Pago.EFECTIVO, monto=7000)

    def test_cierre_guarda_esperado_contado_y_diferencia_por_medio(self):
        self.jornada_con_varios_medios()
        contados = {Pago.NEQUI: 15000, Pago.DAVIPLATA: 3000, Pago.TARJETA: 6000, Pago.TRANSFERENCIA: 0}
        with reloj(h(8)):
            cierre = cerrar_caja(77000, contados_medios=contados)

        conteos = {c.metodo: (c.esperado, c.contado, c.diferencia) for c in cierre.conteos.all()}
        self.assertEqual(conteos, {
            Pago.NEQUI: (15000, 15000, 0),
            Pago.DAVIPLATA: (3000, 3000, 0),
            Pago.TARJETA: (7000, 6000, -1000),
            Pago.TRANSFERENCIA: (0, 0, 0),
        })
        # Efectivo: base 50.000 + 20.000 + 7.000 de la dividida.
        self.assertEqual((cierre.efectivo_esperado, cierre.diferencia), (77000, 0))
        self.assertEqual(cierre.diferencia_otros_medios, -1000)
        self.assertEqual(cierre.total_esperado, 77000 + 25000)
        self.assertEqual(cierre.total_contado, 77000 + 24000)
        self.assertEqual(cierre.diferencia_total, -1000)
        self.assertEqual(cierre.ventas_totales, 52000)

    def test_falta_un_medio_o_medio_desconocido_no_cierra(self):
        self.abrir(h(0))
        incompleto = {Pago.NEQUI: 0, Pago.DAVIPLATA: 0, Pago.TARJETA: 0}
        for contados in [incompleto, {**incompleto, Pago.TRANSFERENCIA: 0, Pago.EFECTIVO: 5}]:
            with self.subTest(contados=contados), reloj(h(8)), self.assertRaises(ValidationError):
                cerrar_caja(0, contados_medios=contados)
        self.assertIsNotNone(caja_abierta())
        self.assertFalse(ConteoMedioPago.objects.exists())

    def test_la_base_de_datos_exige_diferencia_coherente(self):
        self.abrir(h(0))
        cierre = self.cerrar(h(8))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConteoMedioPago.objects.create(
                cierre=cierre, metodo=Pago.NEQUI, esperado=100, contado=50, diferencia=0
            )

    def test_pantallas_de_cierre_por_medio(self):
        iniciar_sesion(self.client)
        self.jornada_con_varios_medios()

        respuesta = self.client.get(reverse("finanzas:caja"))
        self.assertContains(respuesta, "Ventas por medio de pago")
        self.assertEqual(respuesta.context["resumen"].ventas_totales, 52000)

        respuesta = self.client.get(reverse("finanzas:cerrar"))
        for medio in MEDIOS_A_CONTAR:
            self.assertContains(respuesta, f'name="contado_{medio}"')
        self.assertEqual(respuesta.context["total_esperado"], 77000 + 25000)

        datos = {"efectivo_contado": "77000", "contado_NEQUI": "15000", "contado_DAVIPLATA": "3000",
                 "contado_TARJETA": "6000"}
        respuesta = self.client.post(reverse("finanzas:cerrar"), datos)
        self.assertContains(respuesta, "Escribe lo que dice Transferencia (0 si no hubo).")
        self.assertIsNotNone(caja_abierta())

        respuesta = self.client.post(
            reverse("finanzas:cerrar"), {**datos, "contado_TRANSFERENCIA": "0"}, follow=True
        )
        self.assertIn(
            "Caja cerrada. Efectivo: cuadra exacto. Otros medios: faltan $ 1.000. "
            "Total: faltan $ 1.000.",
            [str(m) for m in respuesta.context["messages"]],
        )
        self.assertContains(respuesta, "Tarjeta")
        self.assertContains(respuesta, "Faltaron")

        respuesta = self.client.get(reverse("finanzas:cierres"))
        self.assertEqual(respuesta.context["total_diferencias"], -1000)
