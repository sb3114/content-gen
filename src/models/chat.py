import uuid
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel

class AgentConversation(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(default="New Conversation")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    messages: List["AgentMessage"] = Relationship(
        back_populates="conversation",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "AgentMessage.timestamp"}
    )

class AgentMessage(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(foreign_key="agentconversation.id")
    role: str = Field(description="Must be 'user' or 'model'")
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    conversation: AgentConversation = Relationship(back_populates="messages")
