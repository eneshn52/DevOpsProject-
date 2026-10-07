from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Project

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'date') # Panelde hangi sütunların görüneceği
    list_filter = ('status', 'date') # Sağ tarafa filtreleme seçenekleri ekler