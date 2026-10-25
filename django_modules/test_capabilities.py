"""Tests for FR-8 capability profiles and the benchmark import command."""
import io
import json
import tempfile
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase

from .capabilities import get_scores, read_benchmark_results, save_scores
from .models import AIModel, Agent, AgentCapabilityScore

FULL = {'coding': 90, 'reasoning': 85, 'context': 95, 'speed': 50}


class CapabilityScoreTests(TestCase):
    def setUp(self):
        self.agent = Agent.objects.create(name='Coder')

    def test_saved_scores_are_read_back(self):
        save_scores(self.agent, FULL)
        self.assertEqual(get_scores([self.agent.pk]), {self.agent.pk: {k: float(v) for k, v in FULL.items()}})

    def test_saving_again_replaces_old_values(self):
        save_scores(self.agent, FULL)
        save_scores(self.agent, {'speed': 70})
        self.assertEqual(get_scores([self.agent.pk])[self.agent.pk]['speed'], 70.0)
        self.assertEqual(AgentCapabilityScore.objects.filter(agent=self.agent).count(), 4)

    def test_agent_missing_a_score_is_left_out(self):
        save_scores(self.agent, {'coding': 90, 'reasoning': 85, 'context': 95})
        self.assertEqual(get_scores([self.agent.pk]), {})

    def test_invalid_scores_are_rejected_and_nothing_is_saved(self):
        for bad in [{'coding': 150}, {'coding': -1}, {'coding': 'high'}, {'coding': True},
                    {'coding': float('nan')}, {'creativity': 50}, {'coding': 80, 'speed': 101}]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    save_scores(self.agent, bad)
        self.assertFalse(AgentCapabilityScore.objects.exists())


def write_result(folder, name, **data):
    """Write one benchmark result file the way the FR-9 benchmark does."""
    defaults = {'model': 'qwen2.5-coder:7b', 'status': 'completed', 'score_valid': True,
                'updated_at': '2026-10-24T12:00:00+00:00'}
    path = Path(folder) / 'qwen2.5-coder_7b' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**defaults, **data}), encoding='utf-8')
    return path


class BenchmarkImportTests(TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        model = AIModel.objects.create(ollama_name='qwen2.5-coder:7b')
        self.agent = Agent.objects.create(name='Coder', ai_model=model)

    def run_import(self, *args):
        out = io.StringIO()
        call_command('import_benchmark', self.agent.pk, self.folder, *args, stdout=out)
        return out.getvalue()

    def write_all_four(self):
        write_result(self.folder, 'coding.json', coding_score=82.5)
        write_result(self.folder, 'reasoning.json', reasoning_score=75)
        write_result(self.folder, 'context.json', context_score=100)
        write_result(self.folder, 'speed.json', speed_score=64.2)

    def test_import_saves_all_four_scores(self):
        self.write_all_four()
        self.run_import()
        self.assertEqual(get_scores([self.agent.pk])[self.agent.pk],
                         {'coding': 82.5, 'reasoning': 75.0, 'context': 100.0, 'speed': 64.2})

    def test_newest_valid_file_wins(self):
        write_result(self.folder, 'old.json', coding_score=40, updated_at='2026-10-20T10:00:00+00:00')
        write_result(self.folder, 'new.json', coding_score=82.5, updated_at='2026-10-24T10:00:00+00:00')
        write_result(self.folder, 'newer_but_broken.json', coding_score=None, score_valid=False,
                     status='error', updated_at='2026-10-25T10:00:00+00:00')
        self.assertEqual(read_benchmark_results(self.folder, 'qwen2.5-coder:7b')['coding'][0], 82.5)

    def test_other_models_and_unfinished_runs_are_ignored(self):
        write_result(self.folder, 'other.json', model='llama3.1:8b', coding_score=99)
        write_result(self.folder, 'running.json', status='running', coding_score=99)
        (Path(self.folder) / 'broken.json').write_text('{not json', encoding='utf-8')
        self.assertEqual(read_benchmark_results(self.folder, 'qwen2.5-coder:7b'), {})

    def test_missing_metrics_are_reported(self):
        write_result(self.folder, 'coding.json', coding_score=82.5)
        output = self.run_import()
        self.assertIn('speed', output)
        self.assertIn('missing', output)
        self.assertEqual(get_scores([self.agent.pk]), {})  # not fully benchmarked yet

    def test_no_results_is_an_error(self):
        with self.assertRaises(CommandError):
            self.run_import()

    def test_agent_without_model_needs_model_option(self):
        self.agent.ai_model = None
        self.agent.save()
        self.write_all_four()
        with self.assertRaises(CommandError):
            self.run_import()
        self.run_import('--model', 'qwen2.5-coder:7b')
        self.assertIn(self.agent.pk, get_scores([self.agent.pk]))
