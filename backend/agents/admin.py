from django.contrib import admin
from .models import UserPresident, Constitution, State, City, Department, Agency, CivilServantAgent

admin.site.register(UserPresident)
admin.site.register(Constitution)
admin.site.register(State)
admin.site.register(City)
admin.site.register(Department)
admin.site.register(Agency)
admin.site.register(CivilServantAgent)
