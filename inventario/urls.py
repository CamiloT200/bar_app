from django.urls import path

from . import views

app_name = "inventario"

urlpatterns = [
    path("", views.productos, name="productos"),
    path("productos/nuevo/", views.producto_nuevo, name="producto_nuevo"),
    path("productos/<int:pk>/editar/", views.producto_editar, name="producto_editar"),
    path("productos/<int:pk>/movimiento/", views.producto_movimiento, name="producto_movimiento"),
    path("categorias/", views.categorias, name="categorias"),
    path("categorias/crear/", views.categoria_crear, name="categoria_crear"),
    path("categorias/<int:pk>/renombrar/", views.categoria_renombrar, name="categoria_renombrar"),
    path("categorias/<int:pk>/borrar/", views.categoria_borrar, name="categoria_borrar"),
    path("compras/", views.compras, name="compras"),
    path("compras/nueva/", views.compra_nueva, name="compra_nueva"),
    path("compras/<int:pk>/", views.compra, name="compra"),
]
