from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse


class QuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: int = Field(gt=0)
    agent: str
    session_id: str
    method: str
    params: dict
    actor: str = Field(min_length=1)


class FactoryAttention:
    def __init__(self, store, runtimes, children):
        self.store = store
        self.runtimes = runtimes
        self.children = children

    def live(self):
        owners = {}
        for run in self.store.active_dispatches():
            owners.setdefault((run["agent"], run["session_id"]), run["id"])
            for _agent, session_id, _history in self.children(
                run["agent"], run["session_id"]
            ):
                owners.setdefault((run["agent"], session_id), run["id"])
        for agent, service in self.runtimes.keyed_services():
            for request_id, future in list(
                getattr(service, "_pending_client_requests", {}).items()
            ):
                session = getattr(service, "_client_request_sessions", {}).get(
                    request_id
                )
                if session is None or future.done():
                    continue
                payload = getattr(session, "pending_client_requests", {}).get(
                    request_id, {}
                )
                run_id = owners.get((agent, session.id))
                if run_id and payload.get("function") == "escalate":
                    yield service, {
                        "id": request_id,
                        "kind": "escalate",
                        "run_id": run_id,
                        "agent": agent,
                        "session_id": session.id,
                    }
            for request_id, (future, session) in list(
                getattr(service, "_pending_elicitations", {}).items()
            ):
                run_id = owners.get((agent, session.id))
                if run_id and not future.done():
                    yield service, {
                        "id": request_id,
                        "kind": "elicitation",
                        "run_id": run_id,
                        "agent": agent,
                        "session_id": session.id,
                    }

    def listing(self, after=None):
        return {
            **self.store.attention_events(after),
            "questions": [row for _service, row in self.live()],
        }

    async def respond(self, body):
        key = "request_id" if body.method == "client_response" else "elicitation_id"
        expected = "escalate" if body.method == "client_response" else "elicitation"
        if body.method not in {"client_response", "elicitation_response"}:
            raise HTTPException(403, "Only pending question responses are permitted")
        for service, question in self.live():
            if (
                question["id"] == body.params.get(key)
                and question["kind"] == expected
                and question["run_id"] == body.run_id
                and question["agent"] == body.agent
                and question["session_id"] == body.session_id
            ):
                if body.method == "client_response":
                    if set(body.params) - {"request_id", "ok", "result", "error"}:
                        raise HTTPException(422, "Unexpected response fields")
                    return await service.client_response(**body.params)
                if set(body.params) - {"elicitation_id", "action", "value", "reason"}:
                    raise HTTPException(422, "Unexpected response fields")
                return await service.elicitation_response(**body.params)
        raise HTTPException(409, "This question is no longer pending in this run")


def mount_attention(app, attention, controlled):
    @app.get("/integrations/attention")
    async def listing(request: Request, after: int | None = None):
        if not controlled(request):
            return JSONResponse({"detail": "Not authorized"}, status_code=403)
        if after is not None and after < 0:
            raise HTTPException(422, "Invalid cursor")
        return attention.listing(after)

    @app.post("/integrations/attention/respond")
    async def respond(body: QuestionResponse, request: Request):
        if not controlled(request):
            return JSONResponse({"detail": "Not authorized"}, status_code=403)
        return {"result": await attention.respond(body)}
