"""Public communication, independent of runtime transcripts and event journals."""

import json
import time
from fastapi import HTTPException


class ChatMessages:
    def __init__(self, store):
        self.store = store
        with store.transaction():
            store._db.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                chat TEXT NOT NULL, message_id TEXT NOT NULL, role TEXT NOT NULL,
                author TEXT NOT NULL, text TEXT NOT NULL, content TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(chat, role, message_id))""")
            store._db.execute(
                "CREATE INDEX IF NOT EXISTS chat_messages_chat ON chat_messages(chat, seq)"
            )

    def publish(self, chat, message_id, role, author, text, content=None):
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(422, "Write a message first.")
        encoded = json.dumps(content, sort_keys=True)
        with self.store.transaction():
            row = self.store._db.execute(
                "SELECT seq, author, text, content FROM chat_messages WHERE chat=? AND role=? AND message_id=?",
                (chat, role, message_id),
            ).fetchone()
            if row:
                if tuple(row[1:]) != (author, text, encoded):
                    raise HTTPException(
                        409, "This message ID already belongs to a different message."
                    )
                return row[0]
            cursor = self.store._db.execute(
                "INSERT INTO chat_messages(chat,message_id,role,author,text,content,created_at) VALUES(?,?,?,?,?,?,?)",
                (chat, message_id, role, author, text, encoded, time.time()),
            )
            return cursor.lastrowid

    def page(self, chat, after=0, limit=100):
        with self.store.transaction():
            rows = self.store._db.execute(
                "SELECT seq,message_id,role,author,text,content,created_at FROM chat_messages WHERE chat=? AND seq>? ORDER BY seq LIMIT ?",
                (chat, after, limit + 1),
            ).fetchall()
        messages = [
            dict(
                seq=r[0],
                id=r[1],
                role=r[2],
                author=r[3],
                text=r[4],
                content=json.loads(r[5]),
                created_at=r[6],
            )
            for r in rows[:limit]
        ]
        return {
            "messages": messages,
            "cursor": messages[-1]["seq"] if messages else after,
            "has_more": len(rows) > limit,
        }

    def sent_at(self, chats):
        """When people wrote in these chats."""
        chats = list(chats)
        if not chats:
            return []
        with self.store.transaction():
            rows = self.store._db.execute(
                "SELECT created_at FROM chat_messages WHERE role='user' AND chat IN ("
                + ",".join("?" * len(chats))
                + ")",
                chats,
            ).fetchall()
        return [row[0] for row in rows]

    def last(self, chat):
        """The chat's latest published message, for a list's preview."""
        with self.store.transaction():
            row = self.store._db.execute(
                "SELECT role, author, text, created_at FROM chat_messages WHERE chat=? ORDER BY seq DESC LIMIT 1",
                (chat,),
            ).fetchone()
        return dict(role=row[0], author=row[1], text=row[2], at=row[3]) if row else None

    def last_author(self, chat):
        """Who wrote the chat's latest person's message (``None`` if nobody has)."""
        with self.store.transaction():
            row = self.store._db.execute(
                "SELECT author FROM chat_messages WHERE chat=? AND role='user' ORDER BY seq DESC LIMIT 1",
                (chat,),
            ).fetchone()
        return row[0] if row else None

    def delete(self, chat):
        with self.store.transaction():
            self.store._db.execute("DELETE FROM chat_messages WHERE chat=?", (chat,))
