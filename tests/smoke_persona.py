"""Check real Ollama first-person answers through the complete guarded API."""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from server.app import create_app
from server.config import Settings
from server.persona import ROOT

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    with TestClient(create_app(Settings(ROOT/'knowledge/persona.example.json'))) as client:
        client.post('/api/session').raise_for_status()
        for query in ('안녕하세요', '윤서진 자기소개 해줘', 'Atlas에서 맡은 역할은?', '경력에 대해 알려줘'):
            response = client.post('/api/chat', json={'message':query})
            response.raise_for_status()
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert events[-1]['status'] in {'answer','static'}, events[-1]
            answer = ''.join(event.get('text','') for event in events)
            assert not re.search(r'윤서진(?:은|는|이|가|의)|소유자는|사용자는',answer), answer
            # Korean often omits the subject: 'API를 설계했습니다' is natural
            # first-person speech too. Introductions should identify the speaker.
            if query in {'안녕하세요', '윤서진 자기소개 해줘'}:
                assert re.search(r'저는|제가|제 |윤서진입니다',answer), answer
            print(json.dumps({'query':query,'answer':answer},ensure_ascii=False),flush=True)
