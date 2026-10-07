from django.db import models

# Create your models here.
from django.db import models
from django.utils import timezone

class Project(models.Model):
    STATUS_CHOICES = [
        ('NEW', 'Yeni'),
        ('IN_PROGRESS', 'İşlemde'),
        ('DONE', 'Tamamlandı'),
    ]

    # Kullanıcıdan alınacak veriler
    name = models.CharField(max_length=255, verbose_name="Proje Adı") # İstenen "name" alanı[cite: 5]
    requirement_text = models.TextField(verbose_name="Yazılım Gereksinimi")
    
    # Sistem tarafından otomatik atanacak veriler
    date = models.DateTimeField(default=timezone.now, verbose_name="Oluşturulma Tarihi") # İstenen "date" alanı[cite: 5]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NEW', verbose_name="Durum") # İstenen "status" alanı[cite: 5]

    def __str__(self):
        return self.name