from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from .sesion import CLAVE_SESION


class AccesoTests(TestCase):
    url_login = reverse("acceso:login")
    url_logout = reverse("acceso:logout")

    def entrar(self, usuario=None, clave=None, **extra):
        datos = {
            "usuario": settings.BAR_USUARIO if usuario is None else usuario,
            "clave": settings.BAR_CLAVE if clave is None else clave,
        }
        datos.update(extra)
        return self.client.post(self.url_login, datos)

    # 1
    def test_login_correcto_marca_la_sesion_y_va_a_ventas(self):
        respuesta = self.entrar()
        self.assertRedirects(respuesta, reverse("ventas:tablero"))
        self.assertIs(self.client.session[CLAVE_SESION], True)

    # 2
    def test_login_incorrecto_no_marca_la_sesion(self):
        casos = [
            ("laboratorio", "0000"),
            ("otro", settings.BAR_CLAVE),
            ("Laboratorio", settings.BAR_CLAVE),  # distingue mayúsculas
            ("", ""),
        ]
        for usuario, clave in casos:
            with self.subTest(usuario=usuario, clave=clave):
                respuesta = self.entrar(usuario, clave)
                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(respuesta, "Usuario o contraseña incorrectos.")
                self.assertNotIn(CLAVE_SESION, self.client.session)
                # Conserva el usuario, nunca la clave.
                self.assertEqual(respuesta.context["usuario"], usuario)
                self.assertNotContains(respuesta, 'name="clave" value=')

    # 3
    def test_rutas_protegidas_sin_sesion_redirigen_a_login(self):
        for ruta in ["/ventas/", "/inventario/", "/finanzas/", "/ventas/cuentas/1/", "/admin/", "/"]:
            with self.subTest(ruta=ruta):
                respuesta = self.client.get(ruta)
                self.assertRedirects(
                    respuesta, f"/login/?siguiente={ruta.replace('/', '%2F')}",
                    fetch_redirect_response=False,
                )
        # Un POST tampoco pasa: no se crea nada.
        respuesta = self.client.post(reverse("ventas:abrir"), {"identificador": "Mesa 1"})
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(respuesta.url.startswith("/login/"))
        # La pantalla de login sí es accesible.
        self.assertEqual(self.client.get(self.url_login).status_code, 200)

    # 4
    def test_logout_borra_la_sesion(self):
        self.entrar()
        self.assertEqual(self.client.get("/ventas/").status_code, 200)

        respuesta = self.client.post(self.url_logout)
        self.assertRedirects(respuesta, self.url_login)
        self.assertNotIn(CLAVE_SESION, self.client.session)
        respuesta = self.client.get("/ventas/")
        self.assertRedirects(respuesta, "/login/?siguiente=%2Fventas%2F", fetch_redirect_response=False)

    # 5
    def test_siguiente_interno_se_respeta_y_externo_se_ignora(self):
        respuesta = self.entrar(siguiente="/finanzas/")
        self.assertRedirects(respuesta, "/finanzas/")

        self.client.post(self.url_logout)
        respuesta = self.entrar(siguiente="https://malicioso.example.com/")
        self.assertRedirects(respuesta, reverse("ventas:tablero"))

        # El formulario lleva el siguiente que vino en la dirección.
        self.client.post(self.url_logout)
        respuesta = self.client.get(self.url_login, {"siguiente": "/inventario/"})
        self.assertContains(respuesta, 'name="siguiente" value="/inventario/"')

    # 6
    def test_logout_no_acepta_get(self):
        self.entrar()
        self.assertEqual(self.client.get(self.url_logout).status_code, 405)
        self.assertIs(self.client.session[CLAVE_SESION], True)

    # 7
    def test_login_con_sesion_iniciada_redirige_a_ventas(self):
        self.entrar()
        respuesta = self.client.get(self.url_login)
        self.assertRedirects(respuesta, reverse("ventas:tablero"))

    # 8
    def test_login_cambia_la_clave_de_sesion(self):
        self.client.get(self.url_login)
        sesion = self.client.session
        sesion["marca"] = "antes"
        sesion.save()
        clave_antes = sesion.session_key

        self.entrar()
        self.assertNotEqual(self.client.session.session_key, clave_antes)
        # cycle_key conserva los datos, solo cambia la clave.
        self.assertEqual(self.client.session["marca"], "antes")

    # Extra
    def test_admin_usa_el_tema_de_el_laboratorio(self):
        self.entrar()
        # El admin conserva su propio login de Django.
        respuesta = self.client.get("/admin/", follow=True)
        self.assertRedirects(respuesta, "/admin/login/?next=/admin/")
        self.assertContains(respuesta, "css/tema.css")
        self.assertContains(respuesta, "css/tema_admin.css")
        self.assertContains(respuesta, "img/logo.jpeg")
        self.assertContains(respuesta, "El Laboratorio · Administración")
        # Sin el modo claro/oscuro propio del admin, que competiría con el tema.
        self.assertNotContains(respuesta, "admin/css/dark_mode.css")
