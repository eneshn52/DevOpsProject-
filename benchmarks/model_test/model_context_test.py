"""Context retrieval accuracy benchmark (context-retrieval-v2)."""
import argparse
import random
import re
import requests
from benchmark_common import (add_cli_args, choose_models, start_run, save,
                              generate_response, finish_score)

BENCHMARK_VERSION = 'context-retrieval-v2'
GENERATION_OPTIONS = {'temperature': 0, 'seed': 42, 'num_predict': 64, 'num_ctx': 8192}

# Approximate context sizes (in generated text words, not tokenizer tokens).
LEVELS = [('K01',350),('K02',700),('K03',1200),('K04',2000),('K05',3500),('K06',5000)]
QUICK_IDS = {'K01','K03','K05'}

def make_document(task_id, count):
    rng=random.Random(41800+int(task_id[1:]))
    alphabet='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    secret=''.join(rng.choice(alphabet) for _ in range(10))
    words=['system','agent','queue','request','network','status','memory','process','worker','result','task','service']
    fillers=[rng.choice(words) for _ in range(count)]
    # Several complete records require matching the requested record ID,
    # instead of copying the document's only access code.
    for index, fraction in enumerate((.1, .45, .9), 1):
        decoy = ''.join(rng.choice(alphabet) for _ in range(10))
        fillers.insert(int(count * fraction), f'RECORD_D{index:02}_ACCESS_CODE={decoy}')
    # Put the target at different positions and add irrelevant but plausible markers.
    insert_at=int(count*({'K01':.15,'K02':.5,'K03':.85,'K04':.2,'K05':.5,'K06':.8}[task_id]))
    fillers.insert(insert_at,f'RECORD_{task_id}_ACCESS_CODE={secret}')
    for i in range(0,count,400):
        fillers.insert(i,f'LOG_INDEX_{i}=NORMAL')
    return ' '.join(fillers),secret

def ask(model,prompt,url):
    return generate_response(url, model, prompt, GENERATION_OPTIONS, timeout=900)

def main():
    parser = argparse.ArgumentParser(description='FR-8 context retrieval benchmark')
    parser.add_argument('--mode', choices=['quick', 'full'], default='quick')
    add_cli_args(parser)
    args = parser.parse_args()
    try:
        models = choose_models(args)
    except (requests.RequestException, ValueError) as exc:
        parser.error(str(exc))
    levels = [x for x in LEVELS if args.mode == 'full' or x[0] in QUICK_IDS]
    for model in models:
        output, run_id = start_run(args.results_dir, model, 'model_context_test', args.mode)
        print(f'\n=== {model} ({args.mode}) ===', flush=True)
        rows = []
        result = {'model': model, 'benchmark_version': BENCHMARK_VERSION, 'mode': args.mode,
                  'generation_options': GENERATION_OPTIONS, 'document_seed_base': 41800,
                  'run_id': run_id, 'status': 'running', 'tasks': rows}
        save(output, result)
        for tid, count in levels:
            document, expected = make_document(tid, count)
            prompt = (f'Find the value of RECORD_{tid}_ACCESS_CODE in the document below. '
                      f'Answer with ONLY the 10-character code.\nDOCUMENT:\n{document}\nEND DOCUMENT\n'
                      f'What is RECORD_{tid}_ACCESS_CODE?')
            try:
                data, elapsed = ask(model, prompt, args.url)
                answer = data.get('response', '').strip()
                passed = answer == expected
                rows.append({'task': tid, 'approx_words': count, 'expected': expected,
                             'answer': answer, 'passed': passed,
                             'answer_format_valid': bool(re.fullmatch(r'[A-Z2-9]{10}', answer)),
                             'done_reason': data.get('done_reason'),
                             'output_tokens': data.get('eval_count'),
                             'prompt_tokens': data.get('prompt_eval_count'), 'elapsed_seconds': elapsed})
                print(f'{tid} ({count} words): {"PASS" if passed else "FAIL"} ({elapsed}s)', flush=True)
            except (requests.RequestException, ValueError) as exc:
                rows.append({'task': tid, 'approx_words': count, 'passed': False, 'error': str(exc),
                             'error_kind': 'request'})
                print(f'{tid}: ERROR {exc}', flush=True)
            save(output, result)
        score = round(100 * sum(bool(x['passed']) for x in rows) / len(rows), 2)
        finish_score(result, 'context_score', score)
        save(output, result)
        print(f'CONTEXT RETRIEVAL SCORE: {result["context_score"]}/100\nSaved: {output}', flush=True)

if __name__ == '__main__':
    main()
