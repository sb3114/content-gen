from datetime import datetime
import uuid
from typing import Optional

from sqlmodel import SQLModel, Field

class PublishedBlog(SQLModel, table=True):
    __tablename__ = "published_blogs"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    wp_post_id: str = Field(index=True, unique=True)
    title: str
    url: str
    description: Optional[str] = None
    context: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

