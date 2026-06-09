import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

from src.pipeline.agent import get_agent_chat
from src.database import AsyncSessionLocal
from src.models.chat import AgentConversation, AgentMessage
from src.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="src/ui/templates")

class ChatMessageRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


def _make_local_session():
    """Create an isolated async engine+session factory to avoid cross-loop issues."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    Session = sessionmaker(engine, class_=SQLModelAsyncSession, expire_on_commit=False)
    return engine, Session


def execute_tool(func, **kwargs):
    """Executes a tool function inside a clean thread pool if an active event loop is running to prevent loop recursion runtime errors."""
    import concurrent.futures
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(func, **kwargs)
            return future.result()
    else:
        return func(**kwargs)


@router.get("/chat", response_class=HTMLResponse)
async def agent_chat_view(request: Request, id: Optional[uuid.UUID] = None):
    async with AsyncSessionLocal() as session:
        # Fetch all conversations for sidebar (most recent first)
        stmt = select(AgentConversation).order_by(AgentConversation.updated_at.desc())
        conversations = (await session.exec(stmt)).all()

        active_conversation = None
        messages = []

        if id:
            stmt_active = select(AgentConversation).where(AgentConversation.id == id)
            active_conversation = (await session.exec(stmt_active)).first()

        if active_conversation:
            stmt_msgs = (
                select(AgentMessage)
                .where(AgentMessage.conversation_id == active_conversation.id)
                .order_by(AgentMessage.timestamp)
            )
            messages = (await session.exec(stmt_msgs)).all()

    return templates.TemplateResponse(
        "agent.html",
        {
            "request": request,
            "conversations": conversations,
            "active_conversation": active_conversation,
            "messages": messages,
        },
    )


@router.post("/chat/message", response_class=HTMLResponse)
def send_message(
    request: Request,
    message: str = Form(...),
    conversation_id: Optional[str] = Form(None),
):
    import markdown as md

    async def _db_save_and_load():
        local_engine, LocalSession = _make_local_session()
        try:
            async with LocalSession() as session:
                # Create or load conversation
                if not conversation_id or conversation_id in ("None", ""):
                    title = message[:40] + ("..." if len(message) > 40 else "")
                    conv = AgentConversation(title=title)
                    session.add(conv)
                    await session.commit()
                    await session.refresh(conv)
                    c_id = conv.id
                    is_new = True
                    conv_title = title
                else:
                    c_id = uuid.UUID(conversation_id)
                    is_new = False
                    conv_title = ""
                    # Update updated_at
                    stmt_c = select(AgentConversation).where(AgentConversation.id == c_id)
                    conv = (await session.exec(stmt_c)).first()
                    if conv:
                        from datetime import datetime
                        conv.updated_at = datetime.utcnow()
                        session.add(conv)

                # Save user message
                user_msg = AgentMessage(conversation_id=c_id, role="user", content=message)
                session.add(user_msg)
                await session.commit()

                # Fetch full history (excluding the just-saved user msg) for context
                stmt = (
                    select(AgentMessage)
                    .where(AgentMessage.conversation_id == c_id)
                    .order_by(AgentMessage.timestamp)
                )
                all_msgs = (await session.exec(stmt)).all()
                history_dicts = [
                    {"role": m.role, "content": m.content}
                    for m in all_msgs[:-1]  # exclude the new user message
                ]
        finally:
            await local_engine.dispose()

        return c_id, is_new, history_dicts, conv_title

    c_id, is_new, history_dicts, conv_title = asyncio.run(_db_save_and_load())

    # Call agent with history
    chat = get_agent_chat(history_dicts)
    response = chat.send_message(message)

    # Execution loop for function calls
    import json
    import logging
    from google.genai import types
    
    logger = logging.getLogger(__name__)

    def get_function_calls(resp):
        calls = []
        if hasattr(resp, "candidates") and resp.candidates:
            for candidate in resp.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, "function_call") and part.function_call:
                            calls.append(part.function_call)
        return calls

    while True:
        calls = get_function_calls(response)
        if not calls:
            break
            
        parts = []
        for function_call in calls:
            name = function_call.name
            args = function_call.args
            
            logger.info(f"Executing agent tool {name} with args {args}")
            # Map tool function names to local calls
            from src.pipeline.agent import (
                tool_create_jobs,
                tool_list_jobs,
                tool_edit_job,
                tool_delete_job,
                tool_generate_90_day_plan,
                tool_approve_and_schedule_latest_plan,
                tool_list_cluster_plans,
                tool_pause_cluster_plan,
                tool_resume_cluster_plan,
                tool_delete_cluster_plan,
                tool_modify_cluster_plan
            )
            
            try:
                # Clean optional arguments mapping from SDK to match exact function positional/kwargs expectations
                name_clean = name.replace("tool_", "")
                if name_clean == "create_jobs":
                    result = execute_tool(tool_create_jobs, **args)
                elif name_clean == "list_jobs":
                    result = execute_tool(tool_list_jobs, **args)
                elif name_clean == "edit_job":
                    result = execute_tool(tool_edit_job, **args)
                elif name_clean == "delete_job":
                    result = execute_tool(tool_delete_job, **args)
                elif name_clean == "generate_90_day_plan":
                    result = execute_tool(tool_generate_90_day_plan, **args)
                elif name_clean == "approve_and_schedule_latest_plan":
                    result = execute_tool(tool_approve_and_schedule_latest_plan, **args)
                elif name_clean == "list_cluster_plans":
                    result = execute_tool(tool_list_cluster_plans, **args)
                elif name_clean == "pause_cluster_plan":
                    result = execute_tool(tool_pause_cluster_plan, **args)
                elif name_clean == "resume_cluster_plan":
                    result = execute_tool(tool_resume_cluster_plan, **args)
                elif name_clean == "delete_cluster_plan":
                    result = execute_tool(tool_delete_cluster_plan, **args)
                elif name_clean == "modify_cluster_plan":
                    result = execute_tool(tool_modify_cluster_plan, **args)
                else:
                    result = json.dumps({"error": f"Tool '{name}' is not supported."})
            except Exception as e:
                logger.error(f"Error executing tool {name}: {e}", exc_info=True)
                result = json.dumps({"error": str(e)})

            # Convert result to JSON object/dictionary if possible to conform to standard FunctionResponse schema
            try:
                res_obj = json.loads(result)
                if not isinstance(res_obj, dict):
                    res_obj = {"result": res_obj}
            except Exception:
                res_obj = {"result": result}
                
            parts.append(
                types.Part.from_function_response(
                    name=name,
                    response=res_obj
                )
            )
        
        # Send function response parts back to the model to get the next turn
        response = chat.send_message(parts)

    response_text = response.text or ""

    async def _db_save_response():
        local_engine, LocalSession = _make_local_session()
        try:
            async with LocalSession() as session:
                model_msg = AgentMessage(
                    conversation_id=c_id, role="model", content=response_text
                )
                session.add(model_msg)
                await session.commit()
        finally:
            await local_engine.dispose()

    asyncio.run(_db_save_response())

    # Render model response as markdown
    model_html = md.markdown(response_text, extensions=["fenced_code", "nl2br"])

    html_content = f"""
    <div class="msg msg--user">
        <div class="msg__bubble">{message}</div>
    </div>
    <div class="msg msg--model">
        <div class="msg__avatar">AI</div>
        <div class="msg__bubble">{model_html}</div>
    </div>
    """

    headers = {}
    if is_new:
        # OOB swap: prepend new conversation to sidebar list
        sidebar_oob = f"""
        <div id="conv-list" hx-swap-oob="afterbegin">
            <a href="/chat?id={c_id}" class="conv-item conv-item--active" id="conv-{c_id}">
                <span class="conv-icon">💬</span>
                <span class="conv-title">{conv_title}</span>
            </a>
        </div>
        """
        # OOB swap: update hidden conversation_id field
        input_oob = f"""
        <input type="hidden" name="conversation_id" id="active-conv-id" value="{c_id}" hx-swap-oob="true">
        """
        html_content += sidebar_oob + input_oob
        headers["HX-Push-Url"] = f"/chat?id={c_id}"

    return HTMLResponse(content=html_content, headers=headers)


@router.post("/chat/message/stream")
async def chat_message_stream(payload: ChatMessageRequest):
    import json
    import logging
    from src.pipeline.agent import get_agent_chat
    
    logger = logging.getLogger(__name__)
    message = payload.message
    conversation_id = payload.conversation_id

    async def event_generator():
        # 1. DB Save & Load (isolated engine/session)
        local_engine, LocalSession = _make_local_session()
        c_id = None
        is_new = False
        conv_title = ""
        history_dicts = []
        try:
            async with LocalSession() as session:
                # Create or load conversation
                if not conversation_id or conversation_id in ("None", ""):
                    title = message[:40] + ("..." if len(message) > 40 else "")
                    conv = AgentConversation(title=title)
                    session.add(conv)
                    await session.commit()
                    await session.refresh(conv)
                    c_id = conv.id
                    is_new = True
                    conv_title = title
                else:
                    c_id = uuid.UUID(conversation_id)
                    is_new = False
                    # Update updated_at
                    stmt_c = select(AgentConversation).where(AgentConversation.id == c_id)
                    conv = (await session.exec(stmt_c)).first()
                    if conv:
                        from datetime import datetime
                        conv.updated_at = datetime.utcnow()
                        session.add(conv)

                # Save user message
                user_msg = AgentMessage(conversation_id=c_id, role="user", content=message)
                session.add(user_msg)
                await session.commit()

                # Fetch full history (excluding the user msg we just saved)
                stmt = (
                    select(AgentMessage)
                    .where(AgentMessage.conversation_id == c_id)
                    .order_by(AgentMessage.timestamp)
                )
                all_msgs = (await session.exec(stmt)).all()
                history_dicts = [
                    {"role": m.role, "content": m.content}
                    for m in all_msgs[:-1]
                ]
        except Exception as e:
            logger.error(f"Error preparing stream DB context: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            return
        finally:
            await local_engine.dispose()

        # Yield Init event
        yield f"event: init\ndata: {json.dumps({'conversation_id': str(c_id), 'is_new': is_new, 'title': conv_title})}\n\n"

        # Yield Thinking
        yield f"event: thinking\ndata: {json.dumps({'status': 'Analyzing and planning...'})}\n\n"

        # 2. Call agent
        from google.genai import types
        try:
            chat = get_agent_chat(history_dicts)
            response = chat.send_message(message)
        except Exception as e:
            logger.error(f"Error starting agent conversation: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            return

        # Helper to get function calls
        def get_function_calls(resp):
            calls = []
            if hasattr(resp, "candidates") and resp.candidates:
                for candidate in resp.candidates:
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, "function_call") and part.function_call:
                                calls.append(part.function_call)
            return calls

        # 3. Execution loop
        from src.pipeline.agent import (
            tool_create_jobs,
            tool_list_jobs,
            tool_edit_job,
            tool_delete_job,
            tool_generate_90_day_plan,
            tool_approve_and_schedule_latest_plan,
            tool_list_cluster_plans,
            tool_pause_cluster_plan,
            tool_resume_cluster_plan,
            tool_delete_cluster_plan,
            tool_modify_cluster_plan
        )

        while True:
            calls = get_function_calls(response)
            if not calls:
                break

            parts = []
            for function_call in calls:
                name = function_call.name
                args = function_call.args
                
                # Yield tool_start
                yield f"event: tool_start\ndata: {json.dumps({'name': name, 'args': args})}\n\n"
                await asyncio.sleep(0.05) # brief pause for smooth timeline render

                try:
                    name_clean = name.replace("tool_", "")
                    if name_clean == "create_jobs":
                        result = execute_tool(tool_create_jobs, **args)
                    elif name_clean == "list_jobs":
                        result = execute_tool(tool_list_jobs, **args)
                    elif name_clean == "edit_job":
                        result = execute_tool(tool_edit_job, **args)
                    elif name_clean == "delete_job":
                        result = execute_tool(tool_delete_job, **args)
                    elif name_clean == "generate_90_day_plan":
                        result = execute_tool(tool_generate_90_day_plan, **args)
                    elif name_clean == "approve_and_schedule_latest_plan":
                        result = execute_tool(tool_approve_and_schedule_latest_plan, **args)
                    elif name_clean == "list_cluster_plans":
                        result = execute_tool(tool_list_cluster_plans, **args)
                    elif name_clean == "pause_cluster_plan":
                        result = execute_tool(tool_pause_cluster_plan, **args)
                    elif name_clean == "resume_cluster_plan":
                        result = execute_tool(tool_resume_cluster_plan, **args)
                    elif name_clean == "delete_cluster_plan":
                        result = execute_tool(tool_delete_cluster_plan, **args)
                    elif name_clean == "modify_cluster_plan":
                        result = execute_tool(tool_modify_cluster_plan, **args)
                    else:
                        result = json.dumps({"error": f"Tool '{name}' is not supported."})
                except Exception as e:
                    logger.error(f"Error running tool {name}: {e}", exc_info=True)
                    result = json.dumps({"error": str(e)})

                # Yield tool_end
                yield f"event: tool_end\ndata: {json.dumps({'name': name, 'result': result})}\n\n"
                await asyncio.sleep(0.05)

                try:
                    res_obj = json.loads(result)
                    if not isinstance(res_obj, dict):
                        res_obj = {"result": res_obj}
                except Exception:
                    res_obj = {"result": result}

                parts.append(
                    types.Part.from_function_response(
                        name=name,
                        response=res_obj
                    )
                )

            # Send back to model to continue turn
            response = chat.send_message(parts)

        # 4. Stream final text chunk-by-chunk for smooth typing animation
        response_text = response.text or ""
        
        # Simulate typing chunks
        chunk_size = 40
        for i in range(0, len(response_text), chunk_size):
            chunk = response_text[i:i+chunk_size]
            yield f"event: text_chunk\ndata: {json.dumps({'chunk': chunk})}\n\n"
            await asyncio.sleep(0.005)

        # 5. Save model message in DB (isolated session)
        local_engine, LocalSession = _make_local_session()
        try:
            async with LocalSession() as session:
                model_msg = AgentMessage(
                    conversation_id=c_id, role="model", content=response_text
                )
                session.add(model_msg)
                await session.commit()
        except Exception as e:
            logger.error(f"Error saving model response to DB: {e}", exc_info=True)
        finally:
            await local_engine.dispose()

        # Yield Done
        yield f"event: done\ndata: {json.dumps({'status': 'completed'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
