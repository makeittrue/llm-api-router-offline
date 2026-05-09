from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Tuple, List, Dict

import httpx

from app.config import ProviderConfig
from app.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)


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
        """解析Trae DSML格式，转换为标准OpenAI tool_calls格式"""
        if "<｜｜DSML｜｜tool_calls>" not in content:
            return content, []
        
        # 提取DSML工具调用部分
        dsml_pattern = r"<｜｜DSML｜｜tool_calls>(.*?)</｜｜DSML｜｜tool_calls>"
        match = re.search(dsml_pattern, content, re.DOTALL)
        if not match:
            return content, []
        
        dsml_content = match.group(1)
        # 移除DSML部分，剩余内容作为普通文本
        pure_content = re.sub(dsml_pattern, "", content, flags=re.DOTALL).strip()
        
        tool_calls = []
        # 解析每个invoke调用
        invoke_pattern = r'<｜｜DSML｜｜invoke name="(.*?)">(.*?)</｜｜DSML｜｜invoke>'
        for invoke_match in re.finditer(invoke_pattern, dsml_content, re.DOTALL):
            tool_name = invoke_match.group(1)
            invoke_content = invoke_match.group(2)
            
            # 解析参数
            param_pattern = r'<｜｜DSML｜｜parameter name="(.*?)"(?: string=".*?")?>(.*?)</｜｜DSML｜｜parameter>'
            param_match = re.search(param_pattern, invoke_content, re.DOTALL)
            if param_match:
                param_name = param_match.group(1)
                param_value = param_match.group(2).strip()
                # 尝试解析JSON参数
                try:
                    arguments = json.loads(param_value)
                except:
                    arguments = {"content": param_value}
            else:
                arguments = {}
            
            # 构建OpenAI标准tool_call格式
            tool_call = {
                "id": f"call_{tool_name}_{len(tool_calls)}",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments, ensure_ascii=False)
                }
            }
            tool_calls.append(tool_call)
        
        return pure_content, tool_calls

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> dict[str, Any]:
        messages = [m.model_dump(exclude_none=True) for m in request.messages]
        tools = []
        has_tool_calls = False
        
        # 预处理所有消息，提取DSML工具调用，转换成标准tools格式
        for msg in messages:
            if msg.get("role") == "user" and msg.get("content"):
                content = msg["content"]
                if isinstance(content, str) and "<｜｜DSML｜｜tool_calls>" in content:
                    # 解析DSML
                    pure_content, tool_calls = self._parse_dsml_content(content)
                    msg["content"] = pure_content
                    
                    # 转换为标准OpenAI tools格式
                    for tc in tool_calls:
                        try:
                            args = json.loads(tc["function"]["arguments"])
                            # 提取工具参数结构
                            properties = {}
                            required = []
                            for key in args:
                                properties[key] = {
                                    "type": "string",
                                    "description": f"Parameter {key}"
                                }
                                required.append(key)
                            
                            tool_def = {
                                "type": "function",
                                "function": {
                                    "name": tc["function"]["name"],
                                    "description": f"Function {tc['function']['name']}",
                                    "parameters": {
                                        "type": "object",
                                        "properties": properties,
                                        "required": required
                                    }
                                }
                            }
                            tools.append(tool_def)
                            has_tool_calls = True
                        except:
                            continue
        
        payload: dict[str, Any] = {
            "model": provider_model,
            "messages": messages,
        }
        
        # 如果有工具调用，添加tools参数
        if has_tool_calls:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        
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
            "tool_choice"
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

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            
        # 处理Trae DSML格式转换
        if data.get("choices"):
            for choice in data["choices"]:
                if choice.get("message", {}).get("content"):
                    content = choice["message"]["content"]
                    pure_content, tool_calls = self._parse_dsml_content(content)
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

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            yield b"data: [DONE]\n\n"
                            break
                        yield f"data: {data_str}\n\n".encode("utf-8")


def create_provider(config: ProviderConfig) -> BaseProvider:
    provider_map = {
        "openai": OpenAICompatibleProvider,
    }
    cls = provider_map.get(config.api_type, OpenAICompatibleProvider)
    return cls(config)
