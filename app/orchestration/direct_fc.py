"""
Direct function calling — LLM calls tools directly without CrewAI overhead.
Pure OpenAI function-calling with async tool execution.
Pure async: (message, tools) → (response, tool_name, tool_result)
"""
from __future__ import annotations

import json
import logging
import asyncio
from typing import List, Dict, Any, Optional, Tuple

from app.ai_services.llm_manager import llm_manager

logger = logging.getLogger(__name__)


def _tool_to_openai_schema(tool_obj) -> Dict[str, Any]:
    """Convert a tool object to OpenAI function calling schema."""
    # Tools have .name, .description, and .args_schema
    schema = {
        "type": "function",
        "function": {
            "name": tool_obj.name,
            "description": tool_obj.description or "",
        },
    }

    if hasattr(tool_obj, "args_schema") and tool_obj.args_schema:
        try:
            json_schema = tool_obj.args_schema.model_json_schema()
            # Remove title and description from schema (OpenAI doesn't want them at top level)
            properties = json_schema.get("properties", {})
            required = json_schema.get("required", [])
            schema["function"]["parameters"] = {
                "type": "object",
                "properties": properties,
                "required": required,
            }
        except Exception:
            schema["function"]["parameters"] = {"type": "object", "properties": {}}
    else:
        schema["function"]["parameters"] = {"type": "object", "properties": {}}

    return schema


async def _execute_tool(tool_obj, arguments: Dict[str, Any]) -> str:
    """Execute a tool — try async first, fallback to sync in thread pool."""
    try:
        if asyncio.iscoroutinefunction(getattr(tool_obj, "_run", None)):
            result = await tool_obj._run(**arguments)
        elif hasattr(tool_obj, "arun"):
            result = await tool_obj.arun(**arguments)
        else:
            # Sync fallback — run in thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: tool_obj.run(**arguments))
        return str(result)
    except Exception as e:
        logger.error("Tool execution failed (%s): %s", tool_obj.name, e)
        return f"Error executing {tool_obj.name}: {str(e)}"


# ─── Quick replies per tool ───

TOOL_QUICK_REPLIES: Dict[str, List[Dict[str, str]]] = {
    "scan_markets": [
        {"label": "Analyze top mover", "action": "analyze_top"},
        {"label": "View portfolio", "action": "view_portfolio"},
    ],
    "run_technical_analysis": [
        {"label": "Execute trade", "action": "execute_signal"},
        {"label": "Dismiss signal", "action": "dismiss"},
        {"label": "Analyze another", "action": "analyze_another"},
    ],
    "place_order": [
        {"label": "View portfolio", "action": "view_portfolio"},
        {"label": "Monitor position", "action": "monitor"},
        {"label": "Scan more", "action": "scan_markets"},
    ],
    "close_position": [
        {"label": "Review trade", "action": "review"},
        {"label": "Scan markets", "action": "scan_markets"},
    ],
    "view_portfolio": [
        {"label": "Scan markets", "action": "scan_markets"},
        {"label": "Check risk", "action": "check_risk"},
    ],
    "check_risk_limits": [
        {"label": "View portfolio", "action": "view_portfolio"},
        {"label": "Adjust limits", "action": "set_risk_params"},
    ],
}


async def direct_function_call(
    message: str,
    tools: List[Any],
    system_prompt: str,
    conversation_history: Optional[List[Dict]] = None,
    model: str = "gpt-4o",
) -> Tuple[str, Optional[str], Optional[str], List[Dict]]:
    """
    Direct function calling — the main orchestration entry point.

    Returns:
        (response_text, tool_name, tool_result, quick_replies)
    """
    # Build tool schemas
    tool_schemas = [_tool_to_openai_schema(t) for t in tools]
    tool_map = {t.name: t for t in tools}

    # Build messages
    messages = [{"role": "system", "content": system_prompt}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": message})

    try:
        client, account_id = await llm_manager.get_client()

        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_schemas if tool_schemas else None,
            tool_choice="auto" if tool_schemas else None,
            max_tokens=1000,
            temperature=0.3,
        )

        llm_manager.record_usage(
            account_id,
            response.usage.total_tokens if response.usage else 500,
        )

        choice = response.choices[0]
        tool_name = None
        tool_result = None
        quick_replies = []

        # Check if LLM wants to call a tool
        if choice.message.tool_calls:
            tool_call = choice.message.tool_calls[0]
            tool_name = tool_call.function.name
            arguments = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}

            logger.info("Tool call: %s(%s)", tool_name, arguments)

            if tool_name in tool_map:
                tool_result = await _execute_tool(tool_map[tool_name], arguments)
                quick_replies = TOOL_QUICK_REPLIES.get(tool_name, [])

                # Second LLM call to format the tool result for the trader
                messages.append(choice.message.model_dump())
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

                format_response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=800,
                    temperature=0.3,
                )
                llm_manager.record_usage(
                    account_id,
                    format_response.usage.total_tokens if format_response.usage else 200,
                )

                response_text = format_response.choices[0].message.content or tool_result
            else:
                logger.warning("Unknown tool called: %s", tool_name)
                response_text = f"Unknown tool: {tool_name}"
        else:
            # No tool call — direct response
            response_text = choice.message.content or "I'm not sure how to help with that."

        return response_text, tool_name, tool_result, quick_replies

    except Exception as e:
        logger.error("Direct FC failed: %s", e)
        return f"I encountered an error: {str(e)}", None, None, []
