from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from acceso.pruebas import iniciar_sesion
from inventario.models import Categoria, MovimientoInventario, Producto
from inventario.servicios import registrar_movimiento_manual

from .models import Cuenta, ItemConsumo, Mesero
from .servicios import abrir_cuenta, agregar_item, cerrar_cuenta, crear_mesero, quitar_item

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


# ---------------------------------------------------------------------------
# Interfaz web
# ---------------------------------------------------------------------------


class CodigoStockInsuficienteTests(BaseVentas):
    # 10 (vistas)
    def test_error_de_stock_lleva_codigo_y_datos(self):
        self.cargar_stock(self.aguila, 1)
        with self.assertRaises(ValidationError) as ctx:
            agregar_item(self.mesa, self.aguila, 3)
        error = ctx.exception
        self.assertEqual(error.code, "stock_insuficiente")
        self.assertEqual(error.params["stock"], 1)
        self.assertEqual(error.params["cantidad"], 3)
        self.assertEqual(error.params["faltante"], 2)
        self.assertIn("hay 1, se piden 3", error.messages[0])


class VistasVentasTests(BaseVentas):
    def setUp(self):
        super().setUp()
        iniciar_sesion(self.client)

    def url(self, nombre, *args):
        return reverse(f"ventas:{nombre}", args=args)

    def mensajes(self, respuesta):
        return [str(m) for m in respuesta.context["messages"]]

    # 1
    def test_tablero_muestra_solo_cuentas_abiertas_con_su_total(self):
        self.cargar_stock(self.aguila, 10)
        agregar_item(self.mesa, self.aguila, 2)
        retirado = agregar_item(self.mesa, self.aguila, 1)
        quitar_item(retirado)
        cerrada = abrir_cuenta("Barra")
        cerrar_cuenta(cerrada)

        respuesta = self.client.get(self.url("tablero"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(list(respuesta.context["cuentas"]), [self.mesa])
        self.assertEqual(respuesta.context["cuentas"][0].subtotal, 8000)
        self.assertContains(respuesta, "$ 8.000")
        self.assertNotContains(respuesta, "Barra")

    # 2
    def test_abrir_cuenta_redirige_y_repetida_muestra_error(self):
        respuesta = self.client.post(self.url("abrir"), {"identificador": "Mesa 7"})
        nueva = Cuenta.objects.get(identificador="Mesa 7")
        self.assertRedirects(respuesta, self.url("cuenta", nueva.pk))

        respuesta = self.client.post(self.url("abrir"), {"identificador": "mesa 7"}, follow=True)
        self.assertRedirects(respuesta, self.url("tablero"))
        self.assertIn("Ya hay una cuenta abierta llamada mesa 7.", self.mensajes(respuesta))
        self.assertEqual(Cuenta.objects.filter(identificador__iexact="mesa 7").count(), 1)

    # 3
    def test_agregar_con_stock_crea_item_y_redirige(self):
        self.cargar_stock(self.aguila, 10)
        respuesta = self.client.post(
            self.url("agregar", self.mesa.pk),
            {"producto": self.aguila.pk, "cantidad": 6},
            follow=True,
        )
        self.assertRedirects(respuesta, self.url("cuenta", self.mesa.pk))
        item = ItemConsumo.objects.get()
        self.assertEqual((item.cantidad, item.precio_unitario), (6, 4000))
        self.assertIn("6 × Águila agregada(s)", self.mensajes(respuesta))
        self.assertEqual(respuesta.context["deshacer"], item)
        self.assertEqual(self.aguila.stock, 4)

    # 4
    def test_agregar_sin_stock_muestra_pantalla_de_forzar(self):
        self.cargar_stock(self.aguila, 1)
        respuesta = self.client.post(
            self.url("agregar", self.mesa.pk), {"producto": self.aguila.pk, "cantidad": 3}
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "ventas/forzar.html")
        self.assertEqual(respuesta.context["stock"], 1)
        self.assertEqual(respuesta.context["faltante"], 2)
        self.assertContains(respuesta, "ajuste de inventario de +2")
        self.assertFalse(ItemConsumo.objects.exists())
        self.assertEqual(self.aguila.stock, 1)

    # 5
    def test_forzar_exige_nota_y_con_nota_registra_ajuste(self):
        self.cargar_stock(self.aguila, 1)
        datos = {"producto": self.aguila.pk, "cantidad": 3, "forzar": "on", "nota": "   "}
        respuesta = self.client.post(self.url("agregar", self.mesa.pk), datos)
        self.assertTemplateUsed(respuesta, "ventas/forzar.html")
        self.assertContains(respuesta, "requiere una nota")
        self.assertFalse(ItemConsumo.objects.exists())

        datos["nota"] = "Había en la nevera"
        respuesta = self.client.post(self.url("agregar", self.mesa.pk), datos, follow=True)
        self.assertRedirects(respuesta, self.url("cuenta", self.mesa.pk))
        item = ItemConsumo.objects.get()
        self.assertEqual(item.movimiento_ajuste.cantidad, 2)
        self.assertEqual(item.movimiento_ajuste.nota, "Había en la nevera")
        self.assertIn(
            "3 × Águila agregada(s) (ajuste de inventario +2 registrado)", self.mensajes(respuesta)
        )
        self.assertEqual(self.aguila.stock, 0)

    # 6
    def test_quitar_item_redirige_y_sale_del_total(self):
        self.cargar_stock(self.aguila, 10)
        agregar_item(self.mesa, self.aguila, 2)
        item = agregar_item(self.mesa, self.aguila, 3)
        respuesta = self.client.post(self.url("quitar", item.pk), follow=True)
        self.assertRedirects(respuesta, self.url("cuenta", self.mesa.pk))
        self.assertEqual(respuesta.context["total"], 8000)
        self.assertIn("Quitado: 3 × Águila", self.mensajes(respuesta))
        self.assertEqual(self.aguila.stock, 8)

    # 7
    def test_cobrar_cierra_la_cuenta_y_sin_metodo_da_error(self):
        self.cargar_stock(self.aguila, 10)
        agregar_item(self.mesa, self.aguila, 2)

        respuesta = self.client.post(self.url("cobrar", self.mesa.pk), {}, follow=True)
        self.assertRedirects(respuesta, self.url("cobrar", self.mesa.pk))
        self.assertIn(
            "Una cuenta con consumo necesita método de pago para cerrarse.",
            self.mensajes(respuesta),
        )
        self.mesa.refresh_from_db()
        self.assertTrue(self.mesa.esta_abierta)

        respuesta = self.client.post(
            self.url("cobrar", self.mesa.pk), {"metodo_pago": Pago.NEQUI}, follow=True
        )
        self.assertRedirects(respuesta, self.url("tablero"))
        self.assertIn("Mesa 3 cobrada: $ 8.000 en Nequi", self.mensajes(respuesta))
        self.mesa.refresh_from_db()
        self.assertEqual((self.mesa.estado, self.mesa.total), (Estado.CERRADA, 8000))

    # 8
    def test_cuenta_cerrada_es_de_solo_lectura(self):
        self.cargar_stock(self.aguila, 10)
        item = agregar_item(self.mesa, self.aguila, 2)
        cerrar_cuenta(self.mesa, Pago.EFECTIVO)

        respuesta = self.client.get(self.url("cuenta", self.mesa.pk))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'id="form-agregar"')
        self.assertNotContains(respuesta, "COBRAR")
        self.assertContains(respuesta, "Cobrada en Efectivo")

        respuesta = self.client.post(
            self.url("agregar", self.mesa.pk), {"producto": self.aguila.pk, "cantidad": 1}, follow=True
        )
        self.assertIn("La cuenta Mesa 3 ya está cerrada.", self.mensajes(respuesta))
        respuesta = self.client.post(self.url("quitar", item.pk), follow=True)
        self.assertIn("La cuenta Mesa 3 ya está cerrada.", self.mensajes(respuesta))
        self.assertEqual(ItemConsumo.objects.count(), 1)
        self.assertEqual(self.aguila.stock, 8)

    # 9
    def test_acciones_que_modifican_no_aceptan_get(self):
        self.cargar_stock(self.aguila, 10)
        item = agregar_item(self.mesa, self.aguila, 1)
        for url in [
            self.url("abrir"),
            self.url("agregar", self.mesa.pk),
            self.url("quitar", item.pk),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)
        # Cobrar sí acepta GET, pero solo muestra la pantalla de pago.
        respuesta = self.client.get(self.url("cobrar", self.mesa.pk))
        self.assertTemplateUsed(respuesta, "ventas/cobrar.html")
        self.mesa.refresh_from_db()
        self.assertTrue(self.mesa.esta_abierta)
        self.assertTrue(item.vigente)


class CantidadesTests(BaseVentas):
    def setUp(self):
        super().setUp()
        iniciar_sesion(self.client)
        self.cargar_stock(self.aguila, 100)
        self.url_agregar = reverse("ventas:agregar", args=[self.mesa.pk])

    def agregar(self, **datos):
        return self.client.post(self.url_agregar, {"producto": self.aguila.pk, **datos}, follow=True)

    def test_botones_de_cantidad_del_1_al_10_mas_15_30_y_otra(self):
        respuesta = self.client.get(reverse("ventas:cuenta", args=[self.mesa.pk]))
        for n in list(range(1, 11)) + [15, 30]:
            self.assertContains(respuesta, f'id="cantidad-{n}" value="{n}"')
        self.assertContains(respuesta, 'id="cantidad-otra" value="otra"')
        self.assertContains(respuesta, 'name="cantidad_otra"')

    def test_cantidad_rapida_y_otra_cantidad(self):
        self.agregar(cantidad="7")
        self.agregar(cantidad="otra", cantidad_otra="24")
        self.assertEqual(
            list(ItemConsumo.objects.order_by("id").values_list("cantidad", flat=True)), [7, 24]
        )
        self.assertEqual(self.aguila.stock, 100 - 7 - 24)

    def test_otra_cantidad_vacia_o_invalida_no_agrega(self):
        for datos, mensaje in [
            ({"cantidad": "otra"}, "Cantidad: Escribe la cantidad."),
            ({"cantidad": "otra", "cantidad_otra": "0"}, None),
            ({"cantidad": "0"}, "Cantidad: La cantidad debe estar entre 1 y 999."),
            ({"cantidad": "abc"}, "Cantidad: La cantidad debe estar entre 1 y 999."),
        ]:
            with self.subTest(datos=datos):
                respuesta = self.agregar(**datos)
                mensajes = [str(m) for m in respuesta.context["messages"]]
                self.assertTrue(any(m.startswith("Cantidad:") for m in mensajes), mensajes)
                if mensaje:
                    self.assertIn(mensaje, mensajes)
        self.assertFalse(ItemConsumo.objects.exists())


class MeserosTests(BaseVentas):
    def setUp(self):
        super().setUp()
        iniciar_sesion(self.client)

    def mensajes(self, respuesta):
        return [str(m) for m in respuesta.context["messages"]]

    def test_crear_mesero_valida_id_unico_y_datos(self):
        carlos = crear_mesero(" M01 ", "  Carlos   Pérez ")
        self.assertEqual((carlos.codigo, carlos.nombre), ("M01", "Carlos Pérez"))
        with self.assertRaises(ValidationError):
            crear_mesero("m01", "Otro")  # mismo ID sin importar mayúsculas
        for codigo, nombre in [("", "Ana"), ("M02", "   ")]:
            with self.subTest(codigo=codigo, nombre=nombre), self.assertRaises(ValidationError):
                crear_mesero(codigo, nombre)
        self.assertEqual(Mesero.objects.count(), 1)

    def test_abrir_cuenta_con_mesero_opcional(self):
        ana = crear_mesero("M02", "Ana")
        con_mesero = abrir_cuenta("Mesa 8", mesero=ana)
        sin_mesero = abrir_cuenta("Barra")
        self.assertEqual(con_mesero.mesero, ana)
        self.assertIsNone(sin_mesero.mesero)

        ana.activo = False
        ana.save()
        with self.assertRaises(ValidationError):
            abrir_cuenta("Mesa 9", mesero=ana)

    def test_agregar_mesero_desde_el_tablero_y_abrir_cuenta(self):
        url_tablero = reverse("ventas:tablero")
        respuesta = self.client.post(
            reverse("ventas:mesero_crear"), {"codigo": "M03", "nombre": "Luis"}, follow=True
        )
        luis = Mesero.objects.get(codigo="M03")
        self.assertRedirects(respuesta, f"{url_tablero}?mesero={luis.pk}")
        self.assertIn("Mesero Luis (M03) agregado.", self.mensajes(respuesta))
        # Queda elegido en el formulario de abrir cuenta.
        self.assertContains(respuesta, f'<option value="{luis.pk}" selected>Luis (M03)</option>', html=True)

        respuesta = self.client.post(
            reverse("ventas:mesero_crear"), {"codigo": "m03", "nombre": "Otro"}, follow=True
        )
        self.assertIn("El ID m03 ya es de Luis.", self.mensajes(respuesta))

        respuesta = self.client.post(
            reverse("ventas:abrir"), {"identificador": "Mesa 10", "mesero": luis.pk}, follow=True
        )
        cuenta = Cuenta.objects.get(identificador="Mesa 10")
        self.assertEqual(cuenta.mesero, luis)
        self.assertContains(respuesta, "Luis")  # la pantalla de la cuenta muestra al mesero

        respuesta = self.client.get(url_tablero)
        self.assertContains(respuesta, '<i class="bi bi-person"></i> Luis', html=True)

    def test_mesero_crear_no_acepta_get(self):
        self.assertEqual(self.client.get(reverse("ventas:mesero_crear")).status_code, 405)


class FiltroPesosTests(TestCase):
    """Extra: formato de dinero en las plantillas."""

    def test_formato(self):
        from .templatetags.formato import pesos

        self.assertEqual(pesos(45000), "$ 45.000")
        self.assertEqual(pesos(0), "$ 0")
        self.assertEqual(pesos(1234567), "$ 1.234.567")
        self.assertEqual(pesos(-1000), "-$ 1.000")
        self.assertEqual(pesos(None), "")
