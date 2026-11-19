"""Run from the repository root: python tests/smoke_conversation.py (requires Ollama)."""
import json
from pathlib import Path
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from server.app import create_app
from server.config import Settings
from server.persona import ROOT
app = create_app(Settings(persona_path=ROOT / "knowledge/persona.example.json"))

with TestClient(app) as client:
    client.post('/api/session').raise_for_status()
    for question, expected in [
        ('Atlas 프로젝트를 소개해 주세요.', 'atlas'),
        ('사용한 기술은?', 'atlas'),
        ('Orbit은?', 'orbit'),
        ('좀 더 자세히 설명해 줘.', 'orbit'),
    ]:
        response = client.post('/api/chat', json={'message': question})
        response.raise_for_status()
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        final = events[-1]
        assert final.get('status') == 'answer', final
        assert expected in [source['id'] for source in final['sources']], final
        answer = ''.join(event.get('text', '') for event in events)
        print(json.dumps({'question': question, 'answer': answer, 'sources': final['sources']}, ensure_ascii=False))
