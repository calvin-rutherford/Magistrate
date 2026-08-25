from django.contrib import admin
from django.urls import path
from . import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/siri-webhook/', views.siri_webhook, name='siri_webhook'),
]
