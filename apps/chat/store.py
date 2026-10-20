"""Users, conversations and messages (SQLAlchemy Core). Every read and write is scoped by ``user_id``."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from folio.logging import log_event, redact_url

metadata = sa.MetaData()
users = sa.Table(
    "chat_users",
    metadata,
    sa.Column("sub", sa.String(255), primary_key=True),
    sa.Column("email", sa.Text),
    sa.Column("name", sa.Text),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_login", sa.DateTime(timezone=True), nullable=False),
)
conversations = sa.Table(
    "chat_conversations",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("user_id", sa.String(255), sa.ForeignKey("chat_users.sub", ondelete="CASCADE"), nullable=False),
    sa.Column("title", sa.Text, nullable=False, default=""),
    sa.Column("pipeline", sa.String(128)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_chat_conversations_user", "user_id", "updated_at"),
)
messages = sa.Table(
    "chat_messages",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("conversation_id", sa.String(36), sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("role", sa.String(16), nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("query", sa.Text),
    sa.Column("citations", sa.JSON, nullable=False, default=list),
    sa.Column("hits", sa.JSON, nullable=False, default=list),
    sa.Column("feedback", sa.String(8)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

_ENGINES: dict[str, sa.Engine] = {}
_CONV = [conversations.c.id, conversations.c.title, conversations.c.pipeline, conversations.c.created_at,
         conversations.c.updated_at]
_MSG = [messages.c[n] for n in ("id", "role", "content", "query", "citations", "hits", "feedback", "created_at")]


def _now() -> datetime:
    return datetime.now(UTC)


class ChatStore:
    def __init__(self, url: str):
        if url not in _ENGINES:
            if url.startswith("sqlite:///") and ":memory:" not in url:
                import pathlib

                pathlib.Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
            _ENGINES[url] = sa.create_engine(url, pool_pre_ping=True)
            log_event("db_init", logging.DEBUG, "apps.chat", database=redact_url(url))
        self.engine = _ENGINES[url]

    def create_all(self) -> None:
        metadata.create_all(self.engine, checkfirst=True)

    def upsert_user(self, sub: str, email: str | None, name: str | None) -> None:
        now = _now()
        with self.engine.begin() as c:
            if c.execute(sa.select(users.c.sub).where(users.c.sub == sub)).first():
                c.execute(users.update().where(users.c.sub == sub).values(email=email, name=name, last_login=now))
            else:
                c.execute(users.insert().values(sub=sub, email=email, name=name, created_at=now, last_login=now))

    def list_conversations(self, user_id: str) -> list[dict[str, Any]]:
        q = sa.select(*_CONV).where(conversations.c.user_id == user_id).order_by(conversations.c.updated_at.desc())
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings()]

    def create_conversation(self, user_id: str, title: str = "", pipeline: str | None = None) -> dict[str, Any]:
        now, cid = _now(), str(uuid.uuid4())
        with self.engine.begin() as c:
            c.execute(conversations.insert().values(id=cid, user_id=user_id, title=title, pipeline=pipeline,
                                                    created_at=now, updated_at=now))
        return self.get_conversation(user_id, cid)

    def get_conversation(self, user_id: str, cid: str) -> dict[str, Any] | None:
        q = sa.select(*_CONV).where(conversations.c.id == cid, conversations.c.user_id == user_id)
        with self.engine.connect() as c:
            row = c.execute(q).mappings().first()
        return dict(row) if row else None

    def rename_conversation(self, user_id: str, cid: str, title: str) -> dict[str, Any] | None:
        with self.engine.begin() as c:
            n = c.execute(conversations.update().where(conversations.c.id == cid, conversations.c.user_id == user_id)
                          .values(title=title, updated_at=_now())).rowcount
        return self.get_conversation(user_id, cid) if n else None

    def delete_conversation(self, user_id: str, cid: str) -> bool:
        with self.engine.begin() as c:
            if not self._owned(c, user_id, cid):
                return False
            c.execute(messages.delete().where(messages.c.conversation_id == cid))
            c.execute(conversations.delete().where(conversations.c.id == cid))
        return True

    def list_messages(self, user_id: str, cid: str) -> list[dict[str, Any]] | None:
        with self.engine.connect() as c:
            if not self._owned(c, user_id, cid):
                return None
            q = sa.select(*_MSG).where(messages.c.conversation_id == cid).order_by(messages.c.id)
            return [dict(r) for r in c.execute(q).mappings()]

    def add_turn(self, user_id: str, cid: str, question: str, answer: dict[str, Any]) -> list[dict[str, Any]]:
        """Store the user question and its answer together (nothing is stored for a failed turn); the first
        question becomes the title when it is empty."""
        now = _now()
        with self.engine.begin() as c:
            ids = []
            for values in ({"role": "user", "content": question},
                           {"role": "assistant", "content": answer["answer"], "query": answer.get("query"),
                            "citations": answer.get("citations") or [], "hits": answer.get("hits") or []}):
                res = c.execute(messages.insert().values(conversation_id=cid, created_at=now,
                                                         **{"citations": [], "hits": [], **values}))
                ids.append(res.inserted_primary_key[0])
            conv = conversations.update().where(conversations.c.id == cid, conversations.c.user_id == user_id)
            c.execute(conv.values(updated_at=now))
            c.execute(conv.where(conversations.c.title == "").values(title=_title(question)))
            rows = c.execute(sa.select(*_MSG).where(messages.c.id.in_(ids)).order_by(messages.c.id)).mappings()
            return [dict(r) for r in rows]

    def set_feedback(self, user_id: str, mid: int, feedback: str | None) -> dict[str, Any] | None:
        owned = sa.select(conversations.c.id).where(conversations.c.user_id == user_id)
        with self.engine.begin() as c:
            n = c.execute(messages.update().where(messages.c.id == mid, messages.c.role == "assistant",
                                                  messages.c.conversation_id.in_(owned))
                          .values(feedback=feedback)).rowcount
            row = c.execute(sa.select(*_MSG).where(messages.c.id == mid)).mappings().first() if n else None
        return dict(row) if row else None

    def truncate(self, user_id: str, cid: str, mid: int) -> bool:
        """Delete message ``mid`` and every later message of the conversation."""
        with self.engine.begin() as c:
            if not self._owned(c, user_id, cid) or not c.execute(
                    sa.select(messages.c.id).where(messages.c.id == mid, messages.c.conversation_id == cid)).first():
                return False
            c.execute(messages.delete().where(messages.c.conversation_id == cid, messages.c.id >= mid))
            c.execute(conversations.update().where(conversations.c.id == cid).values(updated_at=_now()))
        return True

    @staticmethod
    def _owned(c, user_id: str, cid: str) -> bool:
        q = sa.select(conversations.c.id).where(conversations.c.id == cid, conversations.c.user_id == user_id)
        return c.execute(q).first() is not None


def _title(question: str, limit: int = 40) -> str:
    line = " ".join(question.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"
