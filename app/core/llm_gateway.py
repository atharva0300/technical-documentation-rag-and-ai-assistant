import os
import logging
import contextvars
from typing import Any, AsyncGenerator, Dict, List, Optional
import ollama
from app.config import settings

logger = logging.getLogger(__name__)

_llm_call_count_var: contextvars.ContextVar[int] = contextvars.ContextVar("llm_call_count_var", default=0)

class OllamaUnavailableError(Exception):
    """Raised when Ollama LLM service is offline or unreachable."""
    pass

class LLMBudgetExhaustedError(Exception):
    """Raised when LLM call budget is exhausted."""
    pass

class LLMGateway:
    """
    Centralized Gateway for LLM interactions (Ollama / Local models)
    with enforceable request-scoped runtime accounting and hard budget limits.
    """

    def __init__(self, base_url: Optional[str] = None, default_model: Optional[str] = None, num_ctx: Optional[int] = None):
        self.base_url = base_url or settings.OLLAMA_BASE_URL
        self.default_model = default_model or settings.OLLAMA_MODEL
        self.client = ollama.Client(host=self.base_url)
        self.async_client = ollama.AsyncClient(host=self.base_url)
        # Ollama defaults num_ctx to 2048 regardless of what the model actually supports.
        # Qwen2.5:3b natively supports 32768 tokens; 8192 comfortably covers this pipeline's
        # worst-case prompt (~10 merged evidence chunks + system prompt, ~3-3.5k tokens) with
        # headroom for generation, without the KV-cache cost of maxing out the model's window.
        self.num_ctx = num_ctx if num_ctx is not None else settings.OLLAMA_NUM_CTX

    @property
    def llm_call_count(self) -> int:
        return _llm_call_count_var.get()

    @llm_call_count.setter
    def llm_call_count(self, value: int):
        _llm_call_count_var.set(value)

    def reset_call_count(self):
        _llm_call_count_var.set(0)

    def generate(self, prompt: str, system: Optional[str] = None, model: Optional[str] = None) -> str:
        current_calls = _llm_call_count_var.get()
        if current_calls >= settings.MAX_LLM_CALLS:
            raise LLMBudgetExhaustedError(
                f"LLM Call Budget Exhausted: {current_calls} calls made (Max limit: {settings.MAX_LLM_CALLS})"
            )

        _llm_call_count_var.set(current_calls + 1)
        target_model = model or self.default_model
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            response = self.client.chat(model=target_model, messages=messages, options={"num_ctx": self.num_ctx})
            content = response.get("message", {}).get("content", "").strip()
            if content:
                return content
            raise OllamaUnavailableError("Empty response returned from model")
        except Exception as e:
            if isinstance(e, (OllamaUnavailableError, LLMBudgetExhaustedError)):
                raise
            logger.warning(f"Ollama generation failed: {e}")
            raise OllamaUnavailableError(f"Ollama service error: {e}") from e

    async def astream(self, prompt: str, system: Optional[str] = None, model: Optional[str] = None) -> AsyncGenerator[str, None]:
        current_calls = _llm_call_count_var.get()
        if current_calls >= settings.MAX_LLM_CALLS:
            raise LLMBudgetExhaustedError(
                f"LLM Call Budget Exhausted: {current_calls} calls made (Max limit: {settings.MAX_LLM_CALLS})"
            )

        _llm_call_count_var.set(current_calls + 1)
        target_model = model or self.default_model
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response_stream = await self.async_client.chat(
                model=target_model, messages=messages, stream=True, options={"num_ctx": self.num_ctx}
            )
            async for chunk in response_stream:
                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content
        except Exception as e:
            if isinstance(e, (OllamaUnavailableError, LLMBudgetExhaustedError)):
                raise
            logger.warning(f"Ollama streaming failed: {e}")
            raise OllamaUnavailableError(f"Ollama streaming error: {e}") from e

    async def agenerate(
        self,
        prompt: str,
        system: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> str:
        """Async single-turn generation (doesn't block the event loop), with optional sampling
        options such as temperature, seed or num_predict."""
        current_calls = _llm_call_count_var.get()
        if current_calls >= settings.MAX_LLM_CALLS:
            raise LLMBudgetExhaustedError(
                f"LLM Call Budget Exhausted: {current_calls} calls made (Max limit: {settings.MAX_LLM_CALLS})"
            )

        _llm_call_count_var.set(current_calls + 1)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            response = await self.async_client.chat(
                model=model or self.default_model,
                messages=messages,
                options={"num_ctx": self.num_ctx, **(options or {})},
            )
        except Exception as e:
            logger.warning(f"Ollama generation failed: {e}")
            raise OllamaUnavailableError(f"Ollama service error: {e}") from e
        content = (response.get("message", {}) or {}).get("content", "").strip()
        if not content:
            raise OllamaUnavailableError("Empty response returned from model")
        return content

    async def achat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
        num_predict: Optional[int] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        One chat turn with native tool calling (Ollama's `tools` parameter). Returns the
        assistant message as {"content": str, "tool_calls": [{"name": str, "arguments": dict}]}.
        Counts against the same per-request LLM budget as generate()/astream().
        """
        current_calls = _llm_call_count_var.get()
        if current_calls >= settings.MAX_LLM_CALLS:
            raise LLMBudgetExhaustedError(
                f"LLM Call Budget Exhausted: {current_calls} calls made (Max limit: {settings.MAX_LLM_CALLS})"
            )

        _llm_call_count_var.set(current_calls + 1)
        options: Dict[str, Any] = {"num_ctx": self.num_ctx}
        if temperature is not None:
            options["temperature"] = temperature
        if seed is not None:
            options["seed"] = seed
        if num_predict is not None:
            options["num_predict"] = num_predict

        try:
            response = await self.async_client.chat(
                model=model or self.default_model, messages=messages, tools=tools, options=options
            )
        except Exception as e:
            logger.warning(f"Ollama tool-calling chat failed: {e}")
            raise OllamaUnavailableError(f"Ollama service error: {e}") from e

        message = response.get("message", {}) or {}
        tool_calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function", {}) or {}
            tool_calls.append({
                "name": function.get("name"),
                "arguments": dict(function.get("arguments") or {}),
            })
        return {"content": (message.get("content") or "").strip(), "tool_calls": tool_calls}

llm_gateway = LLMGateway()
