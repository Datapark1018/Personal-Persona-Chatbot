"""Local Qwen regression corpus; not a general security benchmark."""
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.config import Settings
from server.guardrails import Guardrails, GuardBlocked, GuardUnavailable
from server.persona import ROOT


async def main():
    guard = Guardrails(Settings(ROOT / 'knowledge/persona.example.json'))
    cases = json.loads((ROOT / 'tests/fixtures/guardrail_cases.json').read_text(encoding='utf-8'))
    failures = 0
    for case in cases:
        started = time.monotonic()
        actual = False
        try:
            if case['stage'] == 'input':
                await guard.check_input(case['text'], [])
            elif case['stage'] == 'context':
                await guard.check_context([{'id':'fixture','visibility':'public','title':'Fixture','text':case['text']}])
            else:
                await guard.check_output('포트폴리오에 대해 알려 주세요.', case['text'])
        except GuardBlocked:
            actual = True
        except GuardUnavailable:
            actual = 'unavailable'
        passed = actual == case['blocked']
        failures += not passed
        print(json.dumps({'id':case['id'], 'passed':passed, 'blocked':actual,
                          'seconds':round(time.monotonic()-started, 2)}), flush=True)
    print(f'{len(cases)-failures}/{len(cases)} cases passed; model={guard.model.model_name}')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
