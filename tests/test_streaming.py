import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from nemoguardrails.types import LLMResponse

from server.app import create_app
from server.config import Settings
from server.persona import ROOT


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_cancels_inference_and_releases_capacity(self):
        await self.check_disconnect('generation')

    async def test_disconnect_cancels_input_guard(self):
        await self.check_disconnect('input')

    async def test_disconnect_cancels_output_guard_before_any_delta(self):
        await self.check_disconnect('output')

    async def check_disconnect(self, stage):
        app = create_app(Settings(ROOT/'knowledge/persona.example.json'))
        sid = 'isolated-test-session'
        app.state.sessions[sid] = {'expires': time.monotonic()+1800, 'history':[], 'topics':[], 'sources':[]}
        started, disconnected, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        body_sent = False

        async def wait_for_cancel(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        guard_calls = 0
        async def check_guard(*args, **kwargs):
            nonlocal guard_calls
            guard_calls += 1
            if stage == 'input' or (stage == 'output' and guard_calls == 3):
                await wait_for_cancel()
            return LLMResponse(content='{"blocked":false}')

        raw = json.dumps({'status':'answer','claims':[{'text':'Atlas 프로젝트입니다.', 'source_ids':['atlas']}]})
        generate = wait_for_cancel if stage == 'generation' else AsyncMock(return_value=raw)

        async def receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {'type':'http.request', 'body':b'{"message":"Atlas"}', 'more_body':False}
            await disconnected.wait()
            return {'type':'http.disconnect'}

        frames = []
        async def send(message):
            frames.append(message)

        scope = {'type':'http', 'asgi':{'version':'3.0','spec_version':'2.3'}, 'http_version':'1.1',
                 'method':'POST', 'scheme':'http', 'path':'/api/chat', 'raw_path':b'/api/chat',
                 'query_string':b'', 'root_path':'', 'server':('testserver',80), 'client':('127.0.0.1',1234),
                 'headers':[(b'host',b'testserver'),(b'cookie',f'session={sid}'.encode()),(b'content-type',b'application/json')]}
        with patch('server.engine.generate', new=generate), patch('server.guardrails.OllamaGuardModel.generate_async', new=check_guard):
            request_task = asyncio.create_task(app(scope, receive, send))
            try:
                await asyncio.wait_for(started.wait(), 3)
                disconnected.set()
                await asyncio.wait_for(request_task, 3)
                await asyncio.wait_for(cancelled.wait(), 3)
            finally:
                if not request_task.done():
                    request_task.cancel()
                    await asyncio.gather(request_task, return_exceptions=True)
        self.assertFalse(app.state.active)
        self.assertFalse(app.state.inference_lock.locked())
        self.assertEqual(app.state.sessions[sid]['history'], [])
        self.assertFalse(any(b'event: delta' in frame.get('body',b'') for frame in frames))


class CapacityTests(unittest.TestCase):
    def test_busy_session_and_global_capacity(self):
        app = create_app(Settings(ROOT/'knowledge/persona.example.json'))
        client = TestClient(app)
        client.post('/api/session')
        sid = client.cookies['session']
        app.state.active.add(sid)
        self.assertEqual(client.post('/api/chat',json={'message':'Atlas'}).status_code,429)
        app.state.active.clear()
        app.state.active.update(str(i) for i in range(5))
        self.assertEqual(client.post('/api/chat',json={'message':'Atlas'}).status_code,503)

    def test_expired_session_and_secure_cookie(self):
        app = create_app(Settings(ROOT/'knowledge/persona.example.json', cookie_secure=True))
        client = TestClient(app, base_url='https://testserver')
        response = client.post('/api/session')
        self.assertIn('Secure',response.headers['set-cookie'])
        sid = client.cookies['session']
        app.state.sessions[sid]['expires'] = time.monotonic()-1
        self.assertEqual(client.get('/api/session').status_code,401)
        self.assertNotIn(sid,app.state.sessions)
