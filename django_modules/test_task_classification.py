"""FR-13 classification, persisted weights, and preservation of pre-existing tasks."""
import math

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from .capabilities import METRICS
from .models import Task, TaskDependency
from .task_weights import DEFAULT_WEIGHTS, TASK_WEIGHTS, capability_weights_for


class TaskClassificationTests(TestCase):
    def test_all_supported_types_store_normalized_weights(self):
        """Every task type has all four scheduler metrics and retains the legacy scalar."""
        self.assertEqual(set(dict(Task.TYPE_CHOICES)), set(TASK_WEIGHTS))
        for task_type, expected in TASK_WEIGHTS.items():
            with self.subTest(task_type=task_type):
                self.assertEqual(set(expected), set(METRICS))
                self.assertTrue(all(math.isfinite(value) and value >= 0 for value in expected.values()))
                self.assertAlmostEqual(sum(expected.values()), 1)
                task = Task.objects.create(title='Classified task', type=task_type, weights=1.2,
                                           capability_weights={'speed': 100})
                task.refresh_from_db()
                self.assertEqual(task.capability_weights, expected)
                self.assertEqual(task.weights, 1.2)

    def test_type_change_reclassifies_but_status_update_does_not(self):
        """Partial type saves refresh weights without changing status-only save semantics."""
        task = Task.objects.create(title='API', type='BACKEND')
        task.type = 'REVIEW'
        task.save(update_fields=iter(['type']))
        task.refresh_from_db()
        self.assertEqual(task.capability_weights, TASK_WEIGHTS['REVIEW'])
        task.type, task.status = 'FRONTEND', 'COMPLETED'
        task.save(update_fields=['status'])
        task.refresh_from_db()
        self.assertEqual((task.type, task.status), ('REVIEW', 'COMPLETED'))
        self.assertEqual(task.capability_weights, TASK_WEIGHTS['REVIEW'])
        task.type = 'DATABASE'
        task.save(update_fields=[])
        task.refresh_from_db()
        self.assertEqual(task.type, 'REVIEW')

    def test_weights_are_independent_and_unknown_legacy_types_keep_fallback(self):
        """Mutating one task's mapping cannot affect policy, other tasks, or legacy types."""
        weights = capability_weights_for('BACKEND')
        weights['coding'] = 0
        self.assertEqual(capability_weights_for('BACKEND')['coding'], 0.6)
        task = Task.objects.create(title='Old task', type='LEGACY_CUSTOM')
        task.refresh_from_db()
        self.assertEqual(task.type, 'LEGACY_CUSTOM')
        self.assertEqual(task.capability_weights, DEFAULT_WEIGHTS)


class TaskClassificationMigrationTests(TransactionTestCase):
    def test_backfill_preserves_existing_tasks_and_dependencies(self):
        """Upgrade legacy tasks without losing scalar weights, status, IDs, or DAG edges."""
        before = [('django_modules', '0007_agent_current_task')]
        executor = MigrationExecutor(connection)
        after = executor.loader.graph.leaf_nodes('django_modules')
        try:
            executor.migrate(before)
            old = executor.loader.project_state(before).apps
            old_task = old.get_model('django_modules', 'Task')
            tasks = [old_task.objects.create(title=kind, type=kind, weights=1.2, status='COMPLETED')
                     for kind in TASK_WEIGHTS.keys() | {'LEGACY_CUSTOM'}]
            edge = old.get_model('django_modules', 'TaskDependency').objects.create(
                task_id=tasks[1].pk, depends_on_id=tasks[0].pk)
            MigrationExecutor(connection).migrate(after)
            for original in tasks:
                task = Task.objects.get(pk=original.pk)
                self.assertEqual((task.title, task.type, task.weights, task.status),
                                 (original.title, original.type, 1.2, 'COMPLETED'))
                self.assertEqual(task.capability_weights, capability_weights_for(original.type))
            self.assertEqual(TaskDependency.objects.get(pk=edge.pk).depends_on_id, tasks[0].pk)
            self.assertEqual(TaskDependency.objects.get(pk=edge.pk).task_id, tasks[1].pk)
        finally:
            MigrationExecutor(connection).migrate(after)
