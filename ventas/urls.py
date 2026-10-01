from django.urls import path

from . import views

app_name = "ventas"

urlpatterns = [
    path("", views.tablero, name="tablero"),
    path("cuentas/abrir/", views.abrir, name="abrir"),
    path("meseros/crear/", views.mesero_crear, name="mesero_crear"),
    path("meseros/<int:pk>/eliminar/", views.mesero_eliminar, name="mesero_eliminar"),
    path("cuentas/<int:pk>/", views.cuenta, name="cuenta"),
    path("cuentas/<int:pk>/agregar/", views.agregar, name="agregar"),
    path("cuentas/<int:pk>/cobrar/", views.cobrar, name="cobrar"),
    path("cuentas/<int:pk>/eliminar/", views.eliminar, name="eliminar"),
    path("items/<int:pk>/quitar/", views.quitar, name="quitar"),
]
