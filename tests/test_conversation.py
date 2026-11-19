import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from nemoguardrails.types import LLMResponse

from server import app as module
from server.conversation import MAX_HISTORY_CHARS, remember, resolve as resolve_topic
from server.config import Settings
from server.persona import ROOT, load_persona
from server.retrieval import KnowledgeIndex

EXAMPLE = ROOT / "knowledge/persona.example.json"
OWNER = load_persona(EXAMPLE)
INDEX = KnowledgeIndex(OWNER)

def resolve(query, topics):
    return resolve_topic(query, topics, INDEX, OWNER)


def reply(source):
    return json.dumps({'status': 'answer', 'claims': [
        {'text': f'{source} 프로젝트에 대한 설명입니다.', 'source_ids': [source]}
    ]})


class ConversationTests(unittest.TestCase):
    def setUp(self):
        guard = patch('server.guardrails.OllamaGuardModel.generate_async', new=AsyncMock(return_value=LLMResponse(content='{"blocked":false}')))
        guard.start()
        self.addCleanup(guard.stop)
        self.app = module.create_app(Settings(EXAMPLE))
        self.client = TestClient(self.app)
        self.client.post('/api/session')

    def test_followup_and_explicit_topic_switch(self):
        model = AsyncMock(side_effect=[reply('atlas'), reply('atlas'), reply('orbit'), reply('orbit')])
        with patch('server.engine.generate', new=model):
            for question in ('Atlas 설명', '사용한 기술은?', 'Orbit은?', '좀 더 자세히'):
                response = self.client.post('/api/chat', json={'message': question})
                self.assertIn('"status": "answer"', response.text)
        calls = model.call_args_list
        self.assertEqual([d['id'] for d in calls[1].args[1]], ['atlas'])
        self.assertEqual([d['id'] for d in calls[3].args[1]], ['orbit'])
        self.assertEqual(calls[1].args[2][0]['content'], 'Atlas 설명')

    def test_history_survives_more_than_three_turns_and_fallback(self):
        model = AsyncMock(return_value=reply('atlas'))
        with patch('server.engine.generate', new=model):
            for question in ('Atlas 설명', '더 자세히', '사용한 기술은?', '역할은?', '짧게 요약', '오늘 날씨 어때?', '그 프로젝트는?'):
                self.client.post('/api/chat', json={'message': question})
        history = model.call_args_list[-1].args[2]
        self.assertEqual(history[0]['content'], 'Atlas 설명')
        self.assertTrue(any(m['content'] == '오늘 날씨 어때?' for m in history))
        self.assertEqual([d['id'] for d in model.call_args_list[-1].args[1]], ['atlas'])

    def test_new_session_cannot_inherit_topic(self):
        with patch('server.engine.generate', new=AsyncMock(return_value=reply('atlas'))):
            self.client.post('/api/chat', json={'message':'Atlas 설명'})
        other = TestClient(self.app)
        other.post('/api/session')
        with patch('server.engine.generate', new_callable=AsyncMock) as model:
            result = other.post('/api/chat', json={'message':'그 프로젝트는?'}).text
            self.assertIn('clarification', result)
            model.assert_not_called()
        self.client.delete('/api/session')
        self.client.post('/api/session')
        self.assertEqual(self.app.state.sessions[self.client.cookies['session']]['history'], [])

    def test_resolve_lists_and_switches(self):
        self.assertEqual(resolve('그중 다른 건?', ['atlas'])[0], ['orbit', 'fieldnotes'])
        self.assertEqual(resolve('두 번째는?', ['orbit', 'fieldnotes'])[0], ['fieldnotes'])
        self.assertIsNotNone(resolve('그 프로젝트는?', ['atlas', 'orbit'])[1])
        self.assertEqual(resolve('경력은?', ['atlas']), ([], None))
        self.assertEqual(resolve('어떤 기술 스택을 사용하나요?', []), ([], None))
        self.assertEqual(resolve('오늘 날씨는?', ['atlas']), ([], None))

    def test_history_budget_keeps_pairs(self):
        state = {'history': []}
        for i in range(20):
            remember(state, str(i) + 'q'*900, 'a'*900)
        self.assertLessEqual(sum(len(m['content']) for m in state['history']), MAX_HISTORY_CHARS)
        self.assertEqual(len(state['history']) % 2, 0)
        self.assertTrue(state['history'][-2]['content'].startswith('19'))
