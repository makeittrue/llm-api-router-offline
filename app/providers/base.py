from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Tuple, List, Dict

import httpx
import tiktoken

from app.config import ProviderConfig
from app.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)


def parse_dsml_content(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    if not content:
        return content, []
    if not re.search(r"<\s*｜\s*｜\s*DSML\s*｜\s*｜\s*tool_calls\s*>", content, re.IGNORECASE):
        return content, []
    dsml_pattern = r"<\s*｜\s*｜\s*DSML\s*｜\s*｜\s*tool_calls\s*>(.*?)<\s*\/\s*｜\s*｜\s*DSML\s*｜\s*｜\s*tool_calls\s*>"
    match = re.search(dsml_pattern, content, re.DOTALL | re.IGNORECASE)
    if not match:
        return content, []
    dsml_content = match.group(1)
    pure_content = re.sub(dsml_pattern, "", content, flags=re.DOTALL).strip()
    tool_calls: List[Dict[str, Any]] = []
    invoke_pattern = r'<\s*｜\s*｜\s*DSML\s*｜\s*｜\s*invoke\s+name\s*=\s*"([^"]+)"\s*>(.*?)<\s*\/\s*｜\s*｜\s*DSML\s*｜\s*｜\s*invoke\s*>'
    for invoke_match in re.finditer(invoke_pattern, dsml_content, re.DOTALL | re.IGNORECASE):
        tool_name = invoke_match.group(1).strip()
        invoke_content = invoke_match.group(2)
        param_pattern = r'<\s*｜\s*｜\s*DSML\s*｜\s*｜\s*parameter\s+name\s*=\s*"([^"]+)"\s*(?:\s+string\s*=\s*"[^"]*")?\s*>(.*?)<\s*\/\s*｜\s*｜\s*DSML\s*｜\s*｜\s*parameter\s*>'
        param_match = re.search(param_pattern, invoke_content, re.DOTALL | re.IGNORECASE)
        if param_match:
            param_value = param_match.group(2).strip()
            try:
                arguments = json.loads(param_value)
            except Exception:
                arguments = {"content": param_value}
        else:
            arguments = {}
        tool_call = {
            "id": f"call_{tool_name}_{len(tool_calls)}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
            },
        }
        tool_calls.append(tool_call)
    return pure_content, tool_calls


class BaseProvider(ABC):
    def __init__(self, config: ProviderConfig):
        self.config = config

    @abstractmethod
    async def chat_completion(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> ChatCompletionResponse: ...

    @abstractmethod
    async def chat_completion_stream(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> AsyncIterator[bytes]: ...


class OpenAICompatibleProvider(BaseProvider):
    def _build_url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        return f"{base}{path}"
    
    def _parse_dsml_content(self, content: str) -> Tuple[str, List[Dict[str, Any]]]:
        return parse_dsml_content(content)

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> dict[str, Any]:
        messages = [m.model_dump(exclude_none=True) for m in request.messages]
        payload: dict[str, Any] = {
            "model": provider_model,
            "messages": messages,
        }
        for field in (
            "temperature",
            "top_p",
            "n",
            "stream",
            "stop",
            "max_tokens",
            "presence_penalty",
            "frequency_penalty",
            "logit_bias",
            "user",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "stream_options",
            "response_format",
        ):
            val = getattr(request, field, None)
            if val is not None:
                payload[field] = val
        return payload

    async def chat_completion(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> ChatCompletionResponse:
        url = self._build_url("/chat/completions")
        headers = self._build_headers()
        payload = self._build_payload(request, provider_model)
        payload["stream"] = False
        
        # 打印请求 payload 用于调试
        print(f"[DEBUG] Sending to {url} payload:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                # 打印响应状态和内容
                print(f"[DEBUG] Response status: {resp.status_code}")
                if resp.status_code >= 400:
                    print(f"[DEBUG] Error response content: {resp.text}")
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            # 更详细的错误信息
            error_detail = f"HTTP {e.response.status_code}: {e.response.text}"
            print(f"[ERROR] {error_detail}")
            raise Exception(error_detail) from e
            
        # 处理Trae DSML格式转换
        if data.get("choices"):
            for choice in data["choices"]:
                if choice.get("message", {}).get("content"):
                    content = choice["message"]["content"]
                    pure_content, tool_calls = parse_dsml_content(content)
                    if tool_calls:
                        choice["message"]["content"] = pure_content
                        choice["message"]["tool_calls"] = tool_calls

        return ChatCompletionResponse(**data)

    async def chat_completion_stream(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> AsyncIterator[bytes]:
        url = self._build_url("/chat/completions")
        headers = self._build_headers()
        payload = self._build_payload(request, provider_model)
        payload["stream"] = True
        
        # 打印请求 payload 用于调试
        print(f"[DEBUG] Sending stream to {url} payload:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    # 打印响应状态
                    print(f"[DEBUG] Stream response status: {resp.status_code}")
                    if resp.status_code >= 400:
                        error_content = await resp.aread()
                        error_text = error_content.decode('utf-8', errors='replace')
                        print(f"[DEBUG] Stream error response content: {error_text}")
                        raise Exception(f"HTTP {resp.status_code}: {error_text}")
                    dsml_buffer = ""
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                yield b"data: [DONE]\n\n"
                                break
                            
                            try:
                                chunk_data = json.loads(data_str)
                                # 处理流式DSML转换
                                if chunk_data.get("choices"):
                                    for choice in chunk_data["choices"]:
                                        delta = choice.get("delta", {})
                                        if delta.get("content"):
                                            content = delta["content"]
                                            dsml_buffer += content
                                            
                                            # 检查是否有完整的DSML标签（兼容转义格式）
                                            has_start = re.search(r"<\s*｜\s*｜\s*DSML\s*｜\s*｜\s*tool_calls\s*>", dsml_buffer, re.IGNORECASE)
                                            has_end = re.search(r"<\s*\/\s*｜\s*｜\s*DSML\s*｜\s*｜\s*tool_calls\s*>", dsml_buffer, re.IGNORECASE)
                                            
                                            if has_start and has_end:
                                                # 解析完整DSML
                                                pure_content, tool_calls = self._parse_dsml_content(dsml_buffer)
                                                # 先返回纯文本内容
                                                if pure_content:
                                                    delta["content"] = pure_content
                                                    yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n".encode("utf-8")
                                                # 再返回tool_calls块
                                                for tool_call in tool_calls:
                                                    tool_chunk = chunk_data.copy()
                                                    tool_chunk["choices"][0]["delta"] = {
                                                        "tool_calls": [{
                                                            "index": 0,
                                                            "id": tool_call["id"],
                                                            "type": "function",
                                                            "function": {
                                                                "name": tool_call["function"]["name"],
                                                                "arguments": tool_call["function"]["arguments"]
                                                            }
                                                        }]
                                                    }
                                                    yield f"data: {json.dumps(tool_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
                                                dsml_buffer = ""
                                                continue
                                            elif has_start:
                                                # 正在接收DSML，暂时不返回内容
                                                delta["content"] = ""
                                                yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n".encode("utf-8")
                                                continue
                            
                            except:
                                # 解析失败直接透传
                                pass
                            
                            yield f"data: {data_str}\n\n".encode("utf-8")
        except httpx.HTTPStatusError as e:
            # 更详细的错误信息
            error_detail = f"HTTP {e.response.status_code}: {e.response.text}"
            print(f"[ERROR] {error_detail}")
            raise Exception(error_detail) from e


def create_provider(config: ProviderConfig) -> BaseProvider:
    provider_map = {
        "openai": OpenAICompatibleProvider,
    }
    cls = provider_map.get(config.api_type, OpenAICompatibleProvider)
    return cls(config)
