from django.urls import path

from . import views

app_name = "finanzas"

urlpatterns = [
    path("", views.caja, name="caja"),
    path("caja/abrir/", views.abrir, name="abrir"),
    path("caja/cerrar/", views.cerrar, name="cerrar"),
    path("gastos/nuevo/", views.gasto_nuevo, name="gasto_nuevo"),
    path("cierres/", views.cierres, name="cierres"),
    path("cierres/<int:pk>/", views.cierre, name="cierre"),
]
