# Generated for the master-triggered FR-9 benchmark workflow.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('django_modules', '0009_workertaskresult')]

    operations = [
        migrations.CreateModel(
            name='BenchmarkRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('model_name', models.CharField(max_length=100)),
                ('suite_version', models.CharField(default='fr9-v1', max_length=40)),
                ('status', models.CharField(choices=[('RUNNING', 'Running'), ('COMPLETED', 'Completed'), ('FAILED', 'Failed')], default='RUNNING', max_length=20)),
                ('scores', models.JSONField(blank=True, default=dict)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('agent', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='benchmark_runs', to='django_modules.agent')),
            ],
        ),
        migrations.CreateModel(
            name='BenchmarkCase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('metric', models.CharField(max_length=20)),
                ('case_id', models.CharField(max_length=40)),
                ('expected', models.JSONField(default=dict)),
                ('outcome', models.JSONField(blank=True, default=dict)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cases', to='django_modules.benchmarkrun')),
                ('task', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='benchmark_case', to='django_modules.task')),
            ],
            options={'ordering': ['pk']},
        ),
    ]
