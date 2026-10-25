"""FR-8 reasoning benchmark v4.

Examples:
    python model_reasoning_test.py --mode quick
    python model_reasoning_test.py --mode full

The model is instructed to return only a single FINAL_ANSWER line.
This prevents long explanations from exhausting the output budget.
"""
import argparse
import re

import requests
from benchmark_common import (add_cli_args, choose_models, start_run, save,
                              generate_response, finish_score)

BENCHMARK_VERSION = 'reasoning-v4'
GENERATION_OPTIONS = {'temperature': 0, 'seed': 42, 'num_predict': 128, 'num_ctx': 8192}


# (id, level, question, exact expected answer)
TASKS = [
    ('R01', 'easy', 'A sequence starts at 3. Each next term is exactly twice the previous term. What is the fifth term?', '48'),
    ('R02', 'easy', 'All tulips are flowers. Some flowers are red. Must some tulips be red? Answer YES or NO.', 'NO'),
    ('R03', 'easy', 'On the same day and in the same fixed time zone with no clock changes, a train leaves at 09:40 and arrives at 11:15. How many minutes was the trip?', '95'),
    ('R04', 'medium', 'There are 5 boxes. Each box has 4 bags. Each bag has 3 coins. You remove 7 coins. How many remain?', '53'),
    ('R05', 'medium', 'Exactly four runners Ana, Ben, Cem, Deniz finish a race with no ties. Ana finishes before Ben; Ben before Cem; Deniz after Cem. Who finishes second?', 'Ben'),
    ('R06', 'medium', 'Find the smallest positive integer divisible by both 12 and 18.', '36'),
    ('R07', 'medium', 'If exactly one of A and B is true, and A is false, is B true? Answer YES or NO.', 'YES'),
    # v1 mistakenly said "exactly two are ON", contradicting A=ON and B=OFF.
    ('R08', 'hard', 'Three switches A, B, C are either ON or OFF. Exactly one switch is OFF. A is ON and B is OFF. Is C ON? Answer YES or NO.', 'YES'),
    ('R09', 'hard', 'A shop increases a price by 20% then decreases the new price by 20%. Original price is 250. What is the final price?', '240'),
    ('R10', 'hard', 'A 3-digit code uses distinct digits 1, 2, 3. The first digit is not 1. The last digit is 2. The code is greater than 300. What is the code?', '312'),
    ('R11', 'hard', 'A bag has 3 red and 2 blue balls. Two balls are drawn without replacement. What is the probability both are red? Give a reduced fraction.', '3/10'),
    ('R12', 'hard', 'Five tasks A, B, C, D, E: A before C; B before C; C before D; D before E. What is the lexicographically smallest valid ordering? Output 5 letters.', 'ABCDE'),
]
QUICK_IDS = {'R01', 'R02', 'R04', 'R05', 'R06', 'R08', 'R09', 'R10'}

# The marker is a protocol, not a reasoning question. Never infer correctness
# by finding an expected number somewhere in a long explanation.
FINAL_PATTERN = re.compile(r'FINAL_ANSWER\s*:\s*(.+)', re.I)


def extract_final_answer(raw):
    # Strip a complete Markdown fence, but never select a matching answer
    # from multiple conflicting answers or explanatory paragraphs.
    stripped = raw.strip()
    fence = re.fullmatch(r'```(?:text)?\s*\n(.*?)\n```', stripped, re.S | re.I)
    if fence:
        stripped = fence.group(1).strip()
    match = FINAL_PATTERN.fullmatch(stripped)
    answer = (match.group(1) if match else stripped).strip().strip('`').rstrip('.').strip()
    if not re.fullmatch(r'(?:[+-]?\d+(?:/\d+)?|YES|NO|[A-Za-z]{1,12})', answer, flags=re.I):
        return None
    return answer


def ask(session, url, model, question):
    prompt = (
        'Answer the question accurately. Do NOT explain your reasoning, show steps, '
        'or add any extra text. Output EXACTLY one line in the format '
        'FINAL_ANSWER: <answer>.\n'
        'For yes/no use YES or NO; for names use only the name; for numeric answers '
        'use only the number or reduced fraction after the colon.\n'
        f'Question: {question}\n'
    )
    data, elapsed = generate_response(url, model, prompt, GENERATION_OPTIONS, timeout=600, session=session)
    return data['response'], elapsed, data.get('done_reason')


def main():
    parser = argparse.ArgumentParser(description='FR-8 reasoning benchmark v4')
    parser.add_argument('--mode', choices=['quick', 'full'], default='quick')
    add_cli_args(parser)
    args = parser.parse_args()
    try:
        models = choose_models(args)
    except (requests.RequestException, ValueError) as exc:
        parser.error(str(exc))
    tasks = [t for t in TASKS if args.mode == 'full' or t[0] in QUICK_IDS]
    with requests.Session() as session:
        for model in models:
            output, run_id = start_run(args.results_dir, model, 'model_reasoning_test', args.mode)
            print(f'\n=== {model} ({args.mode}) ===', flush=True)
            rows = []
            result = {'model': model, 'benchmark_version': BENCHMARK_VERSION, 'mode': args.mode,
                      'generation_options': GENERATION_OPTIONS,
                      'run_id': run_id, 'status': 'running', 'tasks': rows}
            save(output, result)
            for task_id, level, question, expected in tasks:
                row = {'task': task_id, 'level': level, 'expected': expected}
                try:
                    raw, elapsed, done_reason = ask(session, args.url, model, question)
                    extracted = extract_final_answer(raw)
                    passed = extracted is not None and extracted.casefold() == expected.casefold()
                    row.update(answer=raw, extracted_answer=extracted, passed=passed,
                               elapsed_seconds=elapsed, done_reason=done_reason)
                    print(f'{task_id}: {"PASS" if passed else "FAIL"} ({elapsed}s) | extracted={extracted!r}', flush=True)
                except (requests.RequestException, ValueError, KeyError) as exc:
                    row.update(passed=False, error=str(exc), error_kind='request')
                    print(f'{task_id}: ERROR {exc}', flush=True)
                rows.append(row)
                save(output, result)
            score = round(100 * sum(row['passed'] for row in rows) / len(rows), 2)
            finish_score(result, 'reasoning_score', score)
            save(output, result)
            print(f'REASONING SCORE: {result["reasoning_score"]}/100\nSaved: {output}', flush=True)

if __name__ == '__main__':
    main()
