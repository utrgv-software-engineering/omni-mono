import json
import re
import secrets

from .power_automate_handlers import validate_teams_history
from .power_automate_policy import activation_scope, parse_timestamp


class CardConversations:
    def __init__(self, source):
        self.source = source
        self.database = source.spool.db
        with self.database:
            self.database.execute(
                "CREATE TABLE IF NOT EXISTS teams_card_conversations (token TEXT PRIMARY KEY, source TEXT NOT NULL, agent TEXT NOT NULL, session_id TEXT NOT NULL, event_id TEXT NOT NULL, root_id TEXT, UNIQUE(source, agent, session_id, event_id))"
            )

    def register(self, agent, session_id, event_id):
        if self.source.event_store is None:
            raise ValueError("Teams conversation storage is unavailable")
        if not all(
            isinstance(value, str) and value for value in (agent, session_id, event_id)
        ):
            raise ValueError("Teams notification requires an originating conversation")
        origin = self.source.event_store.session_origin(session_id)
        candidates = [
            name
            for name, policy in self.source.config.sources.items()
            if policy.get("handler", policy.get("provider")) == "teams"
            and policy.get("thread_activation")
        ]
        if origin and origin["provider"] == "teams":
            payload = origin["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            candidates = [
                name for name in candidates if name == payload.get("bridge_source")
            ]
        if len(candidates) != 1:
            raise ValueError(
                "Teams notification requires one unambiguous configured source"
            )
        source = candidates[0]
        with self.database:
            self.database.execute(
                "INSERT OR IGNORE INTO teams_card_conversations (token,source,agent,session_id,event_id) VALUES (?,?,?,?,?)",
                (secrets.token_hex(32), source, agent, session_id, event_id),
            )
        return self.database.execute(
            "SELECT token FROM teams_card_conversations WHERE source=? AND agent=? AND session_id=? AND event_id=?",
            (source, agent, session_id, event_id),
        ).fetchone()[0]

    def observe(self, delivery, policy):
        if not policy.get("thread_activation") or self.source.event_store is None:
            return
        payload = delivery.payload
        if any(
            payload.get(key) != policy.get(key)
            for key in ("tenant_id", "team_id", "channel_id")
        ):
            return
        history = payload.get("thread_history")
        if not isinstance(history, dict) or not isinstance(history.get("root"), dict):
            return
        root = history["root"]
        application = (root.get("from") or {}).get("application") or {}
        if not application.get("id"):
            return
        root_id = payload.get("root_message_id")
        if root.get("id") != root_id:
            return
        encoded = json.dumps(
            {"body": root.get("body"), "attachments": root.get("attachments")}
        )
        tokens = set(re.findall(r"\[omni-conversation:([0-9a-f]{64})\]", encoded))
        if len(tokens) != 1:
            return
        row = self.database.execute(
            "SELECT source,agent,session_id,root_id FROM teams_card_conversations WHERE token=?",
            (next(iter(tokens)),),
        ).fetchone()
        if row is None or row[0] != delivery.source or row[3] not in (None, root_id):
            return
        validate_teams_history(history, payload)
        created_at = parse_timestamp(root.get("createdDateTime"))
        if created_at < parse_timestamp(policy["thread_activation"]["not_before"]):
            return
        thread_key = json.dumps(
            [
                delivery.source,
                policy["thread_activation"]["factory_id"],
                policy["tenant_id"],
                policy["team_id"],
                policy["channel_id"],
                root_id,
            ],
            separators=(",", ":"),
        )
        existing = self.source.event_store.thread_session("teams", thread_key, row[1])
        if existing is not None and existing != row[2]:
            raise ValueError("Teams card already belongs to another conversation")
        self.source.event_store.remember_thread("teams", thread_key, row[1], row[2])
        with self.database:
            self.database.execute(
                "UPDATE teams_card_conversations SET root_id=? WHERE token=? AND root_id IS NULL",
                (root_id, next(iter(tokens))),
            )
        scope = activation_scope(delivery.source, policy, root_id)
        self.source.threads.accepted(
            scope, "card:" + root_id, created_at.isoformat(), delivery.id
        )

    def conversation(self, source, root_id):
        rows = self.database.execute(
            "SELECT agent,session_id FROM teams_card_conversations WHERE source=? AND root_id=?",
            (source, root_id),
        ).fetchall()
        if len(rows) != 1:
            return None
        return {"agent": rows[0][0], "session_id": rows[0][1]}
