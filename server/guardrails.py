"""NeMo built-in self-check actions, using the existing local Ollama endpoint.

Only the guard actions run here; NeMo does not own chat history, RAG or generation.
No Colang engine, embedding model, external moderation service or telemetry is started.
"""
import asyncio
import json
import logging
import re
import unicodedata

import httpx
from nemoguardrails import RailsConfig
from nemoguardrails.actions.rail_outcome import RailDecision
from nemoguardrails.library.self_check.input_check.actions import self_check_input
from nemoguardrails.library.self_check.output_check.actions import self_check_output
from nemoguardrails.llm.taskmanager import LLMTaskManager
from nemoguardrails.types import LLMResponse
from pydantic import BaseModel, ConfigDict

from .persona import ROOT


class GuardBlocked(Exception):
    def __init__(self, stage):
        self.reason = {"input": "unsafe_request", "context": "unsafe_context", "output": "unsafe_output"}[stage]
        super().__init__(self.reason)


class GuardUnavailable(Exception):
    """A failed check must never be treated as an allow decision."""


class GuardVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    blocked: bool


def read_verdict(raw):
    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate guard field")
            obj[key] = value
        return obj
    if not isinstance(raw, str) or len(raw) > 256:
        raise ValueError("invalid guard verdict size")
    return GuardVerdict.model_validate(json.loads(raw, object_pairs_hook=unique_object))


def parse_verdict(raw):
    verdict = read_verdict(raw)
    return not verdict.blocked, []


def canonical(text):
    text = unicodedata.normalize("NFKC", text)
    return " ".join("".join(c for c in text if not unicodedata.category(c).startswith("C") or c in "\n\t").split())


class OllamaGuardModel:
    """Public NeMo LLMModel protocol; constrained JSON and bounded local calls."""
    def __init__(self, settings):
        self.settings = settings

    @property
    def model_name(self):
        return self.settings.ollama_model

    @property
    def provider_name(self):
        return "ollama"

    @property
    def provider_url(self):
        return self.settings.ollama_url

    async def generate_async(self, prompt, *, stop=None, **kwargs):
        messages = ([{"role": "user", "content": prompt}] if isinstance(prompt, str)
                    else [message.to_dict() for message in prompt])
        if sum(len(m["content"].encode("utf-8")) for m in messages) > 28000:
            raise GuardUnavailable("guard input budget exceeded")
        async with httpx.AsyncClient(timeout=self.settings.guard_timeout, trust_env=False) as client:
            response = await client.post(self.provider_url + "/api/chat", json={
                "model": self.model_name, "stream": False, "think": False,
                "format": GuardVerdict.model_json_schema(), "keep_alive": "5m",
                "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 48},
                "messages": messages,
            })
            response.raise_for_status()
            body = response.json()
        if body.get("done") is not True or body.get("done_reason") != "stop":
            raise GuardUnavailable("incomplete guard verdict")
        content = body["message"]["content"]
        # Validate before NeMo logging or parsing can touch arbitrary model text.
        verdict = read_verdict(content)
        return LLMResponse(content=verdict.model_dump_json(), finish_reason="stop")

    async def stream_async(self, prompt, *, stop=None, **kwargs):
        raise NotImplementedError("Guard decisions are never streamed")
        yield  # Implements the asynchronous iterator part of LLMModel.


class Guardrails:
    def __init__(self, settings):
        # NeMo logs raw prompts at INFO. Disable its content-bearing loggers even
        # if the hosting process enables debug logs; our audit records codes only.
        for name in ("nemoguardrails.llm.call", "nemoguardrails.library.self_check.input_check.actions",
                     "nemoguardrails.library.self_check.output_check.actions"):
            logging.getLogger(name).disabled = True
        self.settings = settings
        self.config = RailsConfig.from_content(yaml_content=(ROOT / "guardrails/prompts.yml").read_text(encoding="utf-8"))
        required = {"self_check_input", "self_check_input $variant=retrieved_context", "self_check_output"}
        prompts = self.config.prompts or []
        tasks = {prompt.task: prompt for prompt in prompts}
        if (len(tasks) != len(prompts) or not required.issubset(tasks)
                or any(tasks[task].output_parser != "strict_guard_decision" for task in required)):
            raise ValueError("Guard policy must define all three tasks with the strict parser")
        self.manager = LLMTaskManager(self.config)
        self.manager.register_output_parser(parse_verdict, "strict_guard_decision")
        self.model = OllamaGuardModel(settings)
        patterns = json.loads((ROOT / "guardrails/patterns.json").read_text(encoding="utf-8"))
        if (not isinstance(patterns, dict) or set(patterns) != {"injection", "output"}
                or any(not isinstance(values, list) or not values
                       or any(not isinstance(value, str) or not value for value in values)
                       for values in patterns.values())):
            raise ValueError("Guard pattern configuration is invalid")
        self.patterns = {key: [re.compile(pattern, re.I) for pattern in values] for key, values in patterns.items()}

    def check_patterns(self, text, stage):
        normalized = canonical(text)
        patterns = self.patterns["injection"] + (self.patterns["output"] if stage == "output" else [])
        if any(pattern.search(normalized) for pattern in patterns):
            raise GuardBlocked(stage)

    async def _check(self, stage, payload):
        try:
            async with asyncio.timeout(self.settings.guard_timeout):
                common = dict(llms={}, llm_task_manager=self.manager, llm=self.model, config=self.config)
                if stage == "output":
                    result = await self_check_output(**common, bot_message=payload)
                else:
                    result = await self_check_input(**common, user_message=payload,
                                                    variant="retrieved_context" if stage == "context" else None)
            if result.failed or result.decision not in {RailDecision.ALLOW, RailDecision.BLOCK}:
                raise GuardUnavailable("invalid guard outcome")
            if result.decision == RailDecision.BLOCK:
                raise GuardBlocked(stage)
        except GuardBlocked:
            raise
        except Exception:
            # Do not propagate prompts, provider errors or malformed responses.
            raise GuardUnavailable("guard check failed") from None

    async def check_input(self, query, history):
        self.check_patterns(query, "input")
        recent = list(history)
        payload = json.dumps({"current_message": canonical(query), "history": recent}, ensure_ascii=False)
        while recent and len(payload.encode("utf-8")) > 20000:
            recent = recent[2:]
            payload = json.dumps({"current_message": canonical(query), "history": recent}, ensure_ascii=False)
        await self._check("input", payload)

    async def check_context(self, documents):
        docs = [{key: d[key] for key in ("id", "title", "text")} for d in documents if d["visibility"] == "public"][:3]
        if not docs:
            raise GuardUnavailable("no public context")
        for doc in docs:
            self.check_patterns(doc["title"] + "\n" + doc["text"], "context")
        await self._check("context", json.dumps(docs, ensure_ascii=False))

    async def check_output(self, query, text):
        self.check_patterns(text, "output")
        await self._check("output", json.dumps({"question": query, "answer": text}, ensure_ascii=False))
