from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from openjiuwen.harness.rails import DeepAgentRail
    _HAS_OJ_RAIL = True
except ImportError:
    _HAS_OJ_RAIL = False
    DeepAgentRail = object

try:
    from openjiuwen.core.foundation.llm.message import SystemMessage
    _HAS_SYSTEM_MESSAGE = True
except ImportError:
    _HAS_SYSTEM_MESSAGE = False
    SystemMessage = None


class RCCAMRail(DeepAgentRail):
    def __init__(self, injector):
        self._injector = injector

    async def before_model_call(self, ctx: Any) -> None:
        try:
            session_key = getattr(ctx, "session", None)
            if session_key is None:
                session_key = getattr(ctx.inputs, "session_key", "") if hasattr(ctx, "inputs") else ""

            user_input = ""
            if hasattr(ctx, "inputs") and hasattr(ctx.inputs, "messages"):
                for msg in ctx.inputs.messages:
                    role = getattr(msg, "role", "") if hasattr(msg, "role") else msg.get("role", "") if isinstance(msg, dict) else ""
                    if role == "user":
                        user_input = getattr(msg, "content", "") if hasattr(msg, "content") else msg.get("content", "") if isinstance(msg, dict) else str(msg)
                        break

            if not user_input:
                return

            state = await self._injector.on_pre_agent_reply(session_key=session_key, user_input=user_input)

            if state.degraded:
                return

            if state.strategy.get("use_cognitive_enhancement", False) and state.retrieved_context:
                context_text = "\n".join(
                    item.get("content", str(item)) if isinstance(item, dict) else str(item)
                    for item in state.retrieved_context
                )
                if context_text:
                    if _HAS_SYSTEM_MESSAGE and SystemMessage is not None:
                        sys_msg = SystemMessage(content=f"[R-CCAM Retrieved Context]\n{context_text}")
                    else:
                        sys_msg = {"role": "system", "content": f"[R-CCAM Retrieved Context]\n{context_text}"}
                    ctx.inputs.messages.insert(0, sys_msg)
        except Exception as e:
            logger.warning(f"RCCAMRail before_model_call error: {e}")

    async def after_model_call(self, ctx: Any) -> None:
        try:
            session_key = getattr(ctx, "session", None)
            if session_key is None:
                session_key = getattr(ctx.inputs, "session_key", "") if hasattr(ctx, "inputs") else ""

            agent_reply = ""
            if hasattr(ctx, "inputs") and hasattr(ctx.inputs, "response"):
                response = ctx.inputs.response
                agent_reply = getattr(response, "content", "") if hasattr(response, "content") else str(response)

            if not agent_reply:
                return

            await self._injector.on_post_agent_reply(session_key=session_key, agent_reply=agent_reply)
        except Exception as e:
            logger.warning(f"RCCAMRail after_model_call error: {e}")
