import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
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


def _make_local_session():
    """Create an isolated async engine+session factory to avoid cross-loop issues."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    Session = sessionmaker(engine, class_=SQLModelAsyncSession, expire_on_commit=False)
    return engine, Session


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
