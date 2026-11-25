import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from nemoguardrails.types import LLMResponse
from pydantic import ValidationError

from server.app import create_app
from server.config import Settings
from server.conversation import resolve
from server.engine import build_messages, system_prompt, validate_answer, greeting
from server.persona import ROOT, Persona, load_persona
from server.retrieval import KnowledgeIndex


def alternate():
    return {
        "fictional": False, "name": "김하늘", "english_name": "Haneul Kim",
        "role": "Data Engineer", "location": "Busan", "headline": "데이터를 읽기 쉽게",
        "bio": "공개 자료로 제품을 만듭니다.", "aliases": ["하늘"],
        "tone": "친근하고 간결하게", "response_mode": "persona",
        "suggested_questions": ["Nimbus 프로젝트를 알려 주세요."],
        "documents": [
            {"id": "nimbus-v2", "title": "Nimbus", "category": "project", "visibility": "public",
             "summary": "공개 데이터 프로젝트", "text": "Nimbus는 Python으로 개발한 데이터 도구입니다.",
             "aliases": ["Nimbus", "님버스"], "tags": ["프로젝트", "데이터"], "url": "https://example.com/nimbus"},
            {"id": "hidden", "title": "PRIVATE_SENTINEL", "category": "project", "visibility": "private",
             "summary": "PRIVATE_SENTINEL", "text": "PRIVATE_SENTINEL", "aliases": ["secret-name"], "tags": ["비밀"]},
        ],
    }


class PersonaTests(unittest.TestCase):
    def test_default_persona_speaks_in_first_person(self):
        data = alternate()
        data.pop('response_mode')
        owner = Persona.model_validate(data)
        self.assertEqual(owner.response_mode, 'persona')
        self.assertIn('김하늘입니다', greeting(owner))
        self.assertNotIn('안내자', greeting(owner))
        prompt = system_prompt(owner)
        self.assertIn('Rewrite third-person source facts', prompt)
        self.assertIn('Use 저는, 제가, 제', prompt)
        owner = owner.model_copy(update={'response_mode':'assistant'})
        self.assertIn('안내자', greeting(owner))
        self.assertIn('third person as their AI portfolio guide', system_prompt(owner))

    def test_invalid_server_origins_are_rejected(self):
        for name in ('OLLAMA_URL', 'PUBLIC_ORIGIN'):
            for value in ('http://localhost:bad', 'http://localhost:99999',
                          'http://local host', 'http://localhost\\evil', 'http://localhost\n'):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name:value}), self.assertRaises(ValueError):
                    Settings.from_env()
        with patch.dict(os.environ, {'OLLAMA_URL':''}), self.assertRaises(ValueError):
            Settings.from_env()

    def setUp(self):
        guard = patch('server.guardrails.OllamaGuardModel.generate_async', new=AsyncMock(return_value=LLMResponse(content='{"blocked":false}')))
        guard.start()
        self.addCleanup(guard.stop)

    def test_private_documents_cannot_cross_boundaries(self):
        owner = Persona.model_validate(alternate())
        index = KnowledgeIndex(owner)
        self.assertEqual(list(index.by_id), ['nimbus-v2'])
        self.assertEqual(index.explicit_projects('secret-name'), [])
        self.assertEqual(index.retrieve('PRIVATE_SENTINEL'), [])
        presentation = json.dumps(owner.presentation(), ensure_ascii=False)
        self.assertNotIn('PRIVATE_SENTINEL', presentation)
        self.assertNotIn('tone', presentation)
        self.assertNotIn('text', presentation)
        messages = build_messages('소개해 주세요', [d.model_dump() for d in owner.documents], [], owner)
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(messages))
        raw = json.dumps({'status':'answer', 'claims':[{'text':'secret', 'source_ids':['hidden']}]})
        with self.assertRaises(ValueError):
            validate_answer(raw, [d.model_dump() for d in owner.documents])

    def test_arbitrary_persona_and_projects(self):
        owner = Persona.model_validate(alternate())
        index = KnowledgeIndex(owner)
        self.assertEqual(index.explicit_projects('님버스 기술은?'), ['nimbus-v2'])
        self.assertEqual(resolve('그 프로젝트는?', ['nimbus-v2'], index, owner)[0], ['nimbus-v2'])
        self.assertEqual(index.retrieve('프로젝트')[0]['id'], 'nimbus-v2')
        prompt = system_prompt(owner)
        self.assertIn('김하늘', prompt)
        self.assertIn('first person', prompt)
        self.assertNotIn('윤서진', prompt)
        self.assertNotIn('fictional examples', prompt)

    def test_schema_rejects_bad_inputs(self):
        mutations = [
            lambda d: d.update(documents=[]),
            lambda d: d['documents'].append(d['documents'][0].copy()),
            lambda d: d['documents'][0].update(text=''),
            lambda d: d['documents'][0].update(text='a'*2001),
            lambda d: d['documents'][0].pop('visibility'),
            lambda d: d['documents'][0].update(visibility='private'),
            lambda d: d['documents'][0].update(id='../escape'),
            lambda d: d.update(unrecognized='value'),
        ]
        for mutate in mutations:
            data = alternate()
            mutate(data)
            with self.subTest(mutation=mutate), self.assertRaises(ValidationError):
                Persona.model_validate(data)
        for url in ('javascript:alert(1)', '//example.com', 'http://example.com', 'https://user:pass@example.com', '/#wrong', 'https://example.com\\evil', 'https://example.com:bad'):
            data = alternate()
            data['documents'][0]['url'] = url
            with self.subTest(url=url), self.assertRaises(ValidationError):
                Persona.model_validate(data)

    def test_errors_do_not_echo_private_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'broken.json'
            data = alternate()
            data['documents'][1]['text'] = {'PRIVATE_SENTINEL': 'secret'}
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError) as error:
                load_persona(path)
            self.assertNotIn('PRIVATE_SENTINEL', str(error.exception))
            self.assertIn('documents.1.text', str(error.exception))

    def test_env_explicit_path_is_not_silently_replaced(self):
        with patch.dict(os.environ, {'PERSONA_PATH':'knowledge/missing.local.json'}):
            settings = Settings.from_env()
            with self.assertRaises(ValueError):
                create_app(settings)

    def test_schema_file_matches_code(self):
        schema = json.loads((ROOT/'knowledge/persona.schema.json').read_text(encoding='utf-8'))
        self.assertEqual(schema, Persona.model_json_schema())

    def test_context_budget_removes_oldest_complete_turns(self):
        owner = Persona.model_validate(alternate())
        history = [{'role': role, 'content': '한글'*1000} for _ in range(12) for role in ('user', 'assistant')]
        messages = build_messages('기술은?', owner.public_documents(), history, owner)
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[-1]['role'], 'user')
        self.assertEqual((len(messages)-2) % 2, 0)
        self.assertLessEqual(sum(len(m['content'].encode('utf-8')) for m in messages), 20000)

    def test_different_apps_and_public_api_are_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'persona.json'
            path.write_text(json.dumps(alternate()), encoding='utf-8')
            app = create_app(Settings(path))
            client = TestClient(app)
            public = client.get('/api/persona').json()
            self.assertEqual(public['name'], '김하늘')
            self.assertEqual([s['id'] for s in public['sections']], ['nimbus-v2'])
            self.assertNotIn('PRIVATE_SENTINEL', json.dumps(public))
            self.assertNotIn('documents', public)
            self.assertEqual(client.get('/knowledge/persona.json').status_code, 404)
            self.assertFalse(client.post('/api/session').json()['fictional'])
            raw = json.dumps({'status':'answer','claims':[{'text':'Python으로 만들었습니다.','source_ids':['nimbus-v2']}]})
            with patch('server.engine.generate', new=AsyncMock(return_value=raw)) as model:
                result = client.post('/api/chat', json={'message':'님버스 설명'}).text
            self.assertIn('https://example.com/nimbus', result)
            self.assertNotIn('hidden', json.dumps(model.call_args.args))
            self.assertEqual(client.get('/api/session').json()['messages'][0]['content'], '님버스 설명')
            other = TestClient(create_app(Settings(path)))
            other.cookies.update(client.cookies)
            self.assertEqual(other.get('/api/session').status_code, 401)
            client.delete('/api/session')
            self.assertEqual(client.get('/api/session').status_code, 401)


class ReadinessTests(unittest.TestCase):
    def test_unavailable_does_not_reveal_backend_details(self):
        client = TestClient(create_app(Settings(ROOT/'knowledge/persona.example.json')))
        with patch('server.app.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value.post = AsyncMock(side_effect=httpx.ConnectError('PRIVATE_BACKEND'))
            response = client.get('/health/ready')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('PRIVATE_BACKEND', response.text)

    def test_readiness_and_liveness_are_separate(self):
        app = create_app(Settings(ROOT/'knowledge/persona.example.json'))
        client = TestClient(app)
        self.assertEqual(client.get('/health/live').status_code, 200)
        response = AsyncMock()
        response.raise_for_status = lambda: None
        response.json = lambda: {'capabilities':['completion']}
        with patch('server.app.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
            self.assertEqual(client.get('/health/ready').status_code, 200)
            self.assertEqual(client.get('/health/ready').status_code, 200)
            self.assertEqual(factory.return_value.__aenter__.return_value.post.await_count, 1)
