import io
import json
from unittest import TestCase
from unittest.mock import patch

from worker.code_review import review_code


class ReviewTests(TestCase):
    def call(self, response=None, status='passed', error=None):
        report = {'source': 'def solve(x): return x', 'status': status, 'cases': [{'detail': '2'}]}
        task = {'instructions': 'Double an integer'}
        with patch('worker.code_review.urllib.request.urlopen', side_effect=error,
                   return_value=io.BytesIO(json.dumps({'response': response}).encode())) as post:
            result = review_code({'ollama_url': 'http://localhost', 'review_model': 'reviewer'}, 'coder', task, report)
        body = json.loads(post.call_args.args[0].data)
        self.assertEqual(body['model'], 'reviewer')
        self.assertEqual(json.loads(body['prompt']), {'task': task, 'execution': report})
        return result

    def test_verdict(self):
        verdict = dict(status='approved', comments=['Returning x preserves the integer.'], issues=[],
                       improvements=[], rejectionReasons=[])
        self.assertEqual(self.call(json.dumps(verdict))['comments'], verdict['comments'])
        self.assertEqual(self.call(json.dumps(verdict), 'failed')['status'], 'rejected')
        verdict.update(status='rejected', rejectionReasons=['Returning x does not double the input.'])
        self.assertEqual(self.call(json.dumps(verdict))['rejectionReasons'], verdict['rejectionReasons'])

    def test_fail_closed(self):
        for response in ('malformed', '{}', 'null', '{"status":"approved"}'):
            self.assertEqual(self.call(response)['status'], 'error')
        for error in (TimeoutError('timed out'), ConnectionError('offline')):
            self.assertIn(str(error), self.call(error=error)['comments'][0])
