from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from datetime import datetime

from .database import Base

class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String(200), nullable=False)

    category = Column(String(100), nullable=False)

    excerpt = Column(String(500), nullable=False)

    body = Column(Text, nullable=False)

    image = Column(String(500))

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    author_id = Column(
        Integer,
        ForeignKey("users.id")
    )

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    username = Column(
        String(100),
        unique=True,
        nullable=False
    )

    password_hash = Column(
        String(255),
        nullable=False
    )

    role = Column(
        String(50),
        default="reader"
    )

class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True)
    
    content = Column(
        Text,
        nullable=False
    )

    author_name = Column(
        String(100),
        nullable=False
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    post_id = Column(
        Integer,
        ForeignKey("posts.id")
    )


class PostImage(Base):
    __tablename__ = "post_images"

    id = Column(Integer, primary_key=True)

    image_path = Column(String(500), nullable=False)

    caption = Column(String(500))

    position = Column(Integer, nullable=False, default=0)

    post_id = Column(
        Integer,
        ForeignKey("posts.id"),
        nullable=False,
        index=True,
    )
