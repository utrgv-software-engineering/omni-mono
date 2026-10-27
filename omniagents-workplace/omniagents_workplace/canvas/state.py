"""Canvases: design boards that people and agents make together.

A canvas is a set of files, as the design canvas of Claude's artifacts has
them: ``canvas.json``, its index, and one ``<Name>.dc.html`` per board (an
HTML page whose ``<x-dc>`` body binds ``{{values}}`` from the
``text/x-dc`` script's component; ``web/canvas-runtime/support.js`` runs
it). The index names every board with its place on the canvas
(``x, y, w, h``), the order they are in, and notes.

Changes are publishes: a publish sends some files (``None`` removes one),
leaves the others as they are, and is made against the version it was built
on; one built on an older version is refused, so whoever made it reads the
newer one and merges. Every publish is a version, kept. People pin comments
to a point on a board.

Every change emits an event (``subscribe``); file contents are bytes the
store keeps as blobs.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "CanvasError",
    "CanvasState",
    "Canvas",
    "CanvasFile",
    "CanvasVersion",
    "CanvasComment",
    "Member",
    "INDEX",
    "read_canvas_dir",
]

INDEX = "canvas.json"
Listener = Callable[[str, Dict[str, Any]], None]

_PATH = re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9._ /-]{1,200}$")
_TEXT_TYPES = (".html", ".json", ".js", ".css", ".md", ".txt", ".svg")
#: A canvas file's largest size, and a canvas's: boards are pages, not media.
MAX_FILE = 2 * 1024 * 1024
MAX_CANVAS = 32 * 1024 * 1024


class CanvasError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Member:
    """Who acts on canvases: a person (their account's id) or an app (its id)."""

    id: str
    name: str
    kind: str  # "person" or "app"
    token: Optional[str] = None
    active: bool = True


@dataclass
class Canvas:
    id: str
    title: str
    owner_id: str
    created_at: float
    updated_at: float
    #: The latest version's number (0: nothing published yet).
    version: int = 0
    #: path -> the blob sha of its current content.
    files: Dict[str, str] = field(default_factory=dict)


@dataclass
class CanvasVersion:
    canvas_id: str
    number: int
    author_id: str
    at: float
    #: The canvas's files at this version: path -> sha.
    files: Dict[str, str]
    changed: List[str]
    label: str = ""


@dataclass
class CanvasComment:
    id: str
    canvas_id: str
    author_id: str
    body: str
    created_at: float
    #: The board it is pinned to, and where on it (its own pixels); none for the canvas.
    board: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    parent_id: Optional[str] = None
    resolved_at: Optional[float] = None
    version: int = 0


@dataclass
class CanvasFile:
    """A file as read: its path, content and the version it is at."""

    path: str
    content: bytes
    sha: str
    version: int

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class CanvasState:
    def __init__(self) -> None:
        self.members: Dict[str, Member] = {}
        self.canvases: Dict[str, Canvas] = {}
        self.versions: Dict[str, CanvasVersion] = {}
        self.comments: Dict[str, CanvasComment] = {}
        #: sha -> bytes: the contents every version's files point at.
        self.blobs: Dict[str, bytes] = {}
        self._listeners: List[Listener] = []
        self._lock = threading.RLock()

    # -- events --------------------------------------------------------------------

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _emit(self, kind: str, **data: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, data)
            except Exception:
                pass

    # -- members ---------------------------------------------------------------------

    def add_member(self, member_id: str, name: str, kind: str) -> Member:
        with self._lock:
            member = self.members.get(member_id)
            if member is None:
                member = Member(id=member_id, name=name, kind=kind)
                self.members[member_id] = member
            else:
                member.name = name
        self._emit("member", member=member)
        return member

    def update_member(self, member: Member, **fields: Any) -> Member:
        with self._lock:
            for key, value in fields.items():
                if key not in ("name", "token", "active"):
                    raise CanvasError(f"Cannot change {key}")
                if (
                    key == "token"
                    and value
                    and any(m.token == value for m in self.members.values())
                ):
                    raise CanvasError("token in use")
                setattr(member, key, value)
        self._emit("member", member=member)
        return member

    def member_for_token(self, token: str) -> Optional[Member]:
        if not token:
            return None
        return next(
            (m for m in self.members.values() if m.token == token and m.active), None
        )

    # -- canvases ---------------------------------------------------------------------

    def _member(self, member_id: str) -> Member:
        member = self.members.get(member_id)
        if member is None or not member.active:
            raise CanvasError("Not a member of the workplace", 403)
        return member

    def canvas(self, ref: str) -> Canvas:
        canvas = self.canvases.get(ref) or next(
            (
                c
                for c in self.canvases.values()
                if c.title.lower() == (ref or "").lower()
            ),
            None,
        )
        if canvas is None:
            raise CanvasError(f'No canvas "{ref}"', 404)
        return canvas

    def create(
        self,
        member_id: str,
        title: str,
        *,
        files: Optional[Dict[str, Optional[bytes]]] = None,
    ) -> Canvas:
        """A new canvas: its index (from ``files``, or an empty one titled ``title``)."""
        self._member(member_id)
        title = (title or "").strip()
        if not title:
            raise CanvasError("A canvas has a title")
        now = time.time()
        with self._lock:
            canvas = Canvas(
                id=uuid.uuid4().hex[:22],
                title=title,
                owner_id=member_id,
                created_at=now,
                updated_at=now,
            )
            self.canvases[canvas.id] = canvas
        self._emit("canvas", canvas=canvas)
        start = dict(files or {})
        if INDEX not in start:
            start[INDEX] = json.dumps(empty_index(title), indent=2).encode()
        self.publish(
            canvas, start, base_version=0, author_id=member_id, label="Created"
        )
        return canvas

    def rename(self, canvas: Canvas, member_id: str, title: str) -> Canvas:
        """A new title, in the canvas and in its index."""
        title = (title or "").strip()
        if not title:
            raise CanvasError("A canvas has a title")
        index = self.index(canvas)
        index["title"] = title
        self.publish(
            canvas,
            {INDEX: json.dumps(index, indent=2).encode()},
            base_version=canvas.version,
            author_id=member_id,
            label="Renamed",
        )
        with self._lock:
            canvas.title = title
        self._emit("canvas", canvas=canvas)
        return canvas

    def delete(self, canvas: Canvas) -> None:
        with self._lock:
            self.canvases.pop(canvas.id, None)
            removed = [("canvas.canvas", canvas.id)]
            for key in [
                k for k, v in self.versions.items() if v.canvas_id == canvas.id
            ]:
                del self.versions[key]
                removed.append(("canvas.version", key))
            for key in [
                k for k, c in self.comments.items() if c.canvas_id == canvas.id
            ]:
                del self.comments[key]
                removed.append(("canvas.comment", key))
            # Contents no version holds any more go with it.
            kept = {sha for v in self.versions.values() for sha in v.files.values()}
            for sha in [s for s in self.blobs if s not in kept]:
                del self.blobs[sha]
        self._emit("canvas_deleted", removed=removed)

    # -- files and versions -------------------------------------------------------------

    def read(
        self, canvas: Canvas, path: str, version: Optional[int] = None
    ) -> CanvasFile:
        files = canvas.files if version is None else self.version(canvas, version).files
        sha = files.get(path)
        if sha is None:
            raise CanvasError(
                f'No file "{path}" in {canvas.title}'
                + (f" at version {version}" if version else ""),
                404,
            )
        return CanvasFile(
            path=path,
            content=self.blobs[sha],
            sha=sha,
            version=canvas.version if version is None else version,
        )

    def index(self, canvas: Canvas, version: Optional[int] = None) -> Dict[str, Any]:
        return json.loads(self.read(canvas, INDEX, version).text)

    def version(self, canvas: Canvas, number: int) -> CanvasVersion:
        found = self.versions.get(f"{canvas.id}:{number}")
        if found is None:
            raise CanvasError(f"{canvas.title} has no version {number}", 404)
        return found

    def history(self, canvas: Canvas) -> List[CanvasVersion]:
        return sorted(
            (v for v in self.versions.values() if v.canvas_id == canvas.id),
            key=lambda v: v.number,
        )

    def publish(
        self,
        canvas: Canvas,
        files: Dict[str, Optional[bytes]],
        *,
        base_version: int,
        author_id: str,
        label: str = "",
    ) -> CanvasVersion:
        """Change ``files`` (``None`` removes one) and keep the rest: the next version.

        Refused when the canvas has moved on from ``base_version`` (read the
        newer files and merge), or when the result's index does not hold."""
        self._member(author_id)
        if not files:
            raise CanvasError("A publish changes at least one file")
        with self._lock:
            if base_version != canvas.version:
                raise CanvasError(
                    f"{canvas.title} is at version {canvas.version}, and this was made on version {base_version}: "
                    f"read version {canvas.version}, merge your change onto it and publish again",
                    409,
                )
            after = dict(canvas.files)
            new_blobs: Dict[str, bytes] = {}
            changed: List[str] = []
            for path, content in files.items():
                if not _PATH.match(path or "") or path.endswith("/"):
                    raise CanvasError(f'"{path}" is not a file path in a canvas')
                if content is None:
                    if path not in after:
                        raise CanvasError(f'No file "{path}" to remove')
                    del after[path]
                    changed.append(path)
                    continue
                if isinstance(content, str):
                    content = content.encode()
                if len(content) > MAX_FILE:
                    raise CanvasError(
                        f'"{path}" is over {MAX_FILE // (1024 * 1024)} MB'
                    )
                sha = hashlib.sha256(content).hexdigest()
                if after.get(path) != sha:
                    after[path] = sha
                    new_blobs[sha] = content
                    changed.append(path)
            if not changed:
                raise CanvasError("Nothing changed: every file sent is as it is")
            blobs = {**self.blobs, **new_blobs}
            if sum(len(blobs[s]) for s in after.values()) > MAX_CANVAS:
                raise CanvasError(
                    f"A canvas holds up to {MAX_CANVAS // (1024 * 1024)} MB"
                )
            previous = (
                json.loads(blobs[canvas.files[INDEX]])
                if INDEX in canvas.files
                else None
            )
            check_index(after, blobs, previous)
            self.blobs.update(new_blobs)
            number = canvas.version + 1
            version = CanvasVersion(
                canvas_id=canvas.id,
                number=number,
                author_id=author_id,
                at=time.time(),
                files=after,
                changed=sorted(changed),
                label=label,
            )
            self.versions[f"{canvas.id}:{number}"] = version
            canvas.files, canvas.version, canvas.updated_at = after, number, version.at
            title = json.loads(blobs[after[INDEX]]).get("title")
            if isinstance(title, str) and title.strip():
                canvas.title = title.strip()
        self._emit("published", canvas=canvas, version=version, blobs=new_blobs)
        return version

    def restore(self, canvas: Canvas, number: int, author_id: str) -> CanvasVersion:
        """A new version with the files of an older one."""
        old = self.version(canvas, number)
        files: Dict[str, Optional[bytes]] = {
            p: self.blobs[s] for p, s in old.files.items() if canvas.files.get(p) != s
        }
        files.update({p: None for p in canvas.files if p not in old.files})
        return self.publish(
            canvas,
            files,
            base_version=canvas.version,
            author_id=author_id,
            label=f"Restored version {number}",
        )

    # -- comments ------------------------------------------------------------------------

    def comment(
        self,
        canvas: Canvas,
        author_id: str,
        body: str,
        *,
        board: Optional[str] = None,
        x: Optional[float] = None,
        y: Optional[float] = None,
        parent_id: Optional[str] = None,
    ) -> CanvasComment:
        self._member(author_id)
        if not (body or "").strip():
            raise CanvasError("A comment has a body")
        with self._lock:
            if parent_id:
                parent = self.comments.get(parent_id)
                if parent is None or parent.canvas_id != canvas.id:
                    raise CanvasError("No such comment", 404)
                parent_id = parent.parent_id or parent.id
                board, x, y = parent.board, parent.x, parent.y
            elif board is not None and board not in canvas.files:
                raise CanvasError(f'No board "{board}" in {canvas.title}')
            note = CanvasComment(
                id=uuid.uuid4().hex,
                canvas_id=canvas.id,
                author_id=author_id,
                body=body.strip(),
                created_at=time.time(),
                board=board,
                x=x,
                y=y,
                parent_id=parent_id,
                version=canvas.version,
            )
            self.comments[note.id] = note
        self._emit("comment", comment=note)
        return note

    def thread(self, canvas: Canvas) -> List[CanvasComment]:
        return sorted(
            (c for c in self.comments.values() if c.canvas_id == canvas.id),
            key=lambda c: c.created_at,
        )

    def resolve(self, note: CanvasComment, resolved: bool) -> CanvasComment:
        root = self.comments.get(note.parent_id or "") or note
        with self._lock:
            root.resolved_at = time.time() if resolved else None
        self._emit("comment", comment=root)
        return root

    def delete_comment(self, note: CanvasComment) -> None:
        with self._lock:
            gone = [
                c.id
                for c in self.comments.values()
                if c.id == note.id or c.parent_id == note.id
            ]
            for cid in gone:
                del self.comments[cid]
        self._emit("comment_deleted", removed=[("canvas.comment", cid) for cid in gone])

    # -- seeds -------------------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        return {
            "members": self.members,
            "canvases": self.canvases,
            "versions": self.versions,
            "comments": self.comments,
            "blobs": sorted(self.blobs),
        }


def empty_index(title: str) -> Dict[str, Any]:
    """A new canvas's index: no boards yet."""
    return {
        "v": 3,
        "title": title,
        "boards": {},
        "order": [],
        "notes": {},
        "attachments": {},
        "pages": [],
        "launch": {"view": "canvas"},
        "designSystems": [],
        "createdOnFiles": {
            "v": 1,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }


def check_index(
    files: Dict[str, str], blobs: Dict[str, bytes], previous: Optional[Dict[str, Any]]
) -> None:
    """The index a publish would leave: version 3, every board a file, ``order`` its boards."""
    if INDEX not in files:
        raise CanvasError(f"A canvas keeps its index, {INDEX}")
    try:
        index = json.loads(blobs[files[INDEX]])
    except ValueError as exc:
        raise CanvasError(f"{INDEX} is not JSON: {exc}")
    if not isinstance(index, dict) or index.get("v") != 3:
        raise CanvasError(f'{INDEX} is version 3 ("v": 3)')
    boards = index.get("boards")
    if not isinstance(boards, dict):
        raise CanvasError(f'{INDEX} has "boards", by file name')
    for name, board in boards.items():
        if name not in files:
            raise CanvasError(
                f'{INDEX} lists the board "{name}", which is not a file in the canvas'
            )
        if not isinstance(board, dict):
            raise CanvasError(f'The board "{name}" is a mapping')
        for key in ("x", "y", "w", "h"):
            if not isinstance(board.get(key), (int, float)) or isinstance(
                board.get(key), bool
            ):
                raise CanvasError(f'The board "{name}" has a number "{key}"')
    order = index.get("order")
    if (
        not isinstance(order, list)
        or sorted(order) != sorted(boards)
        or len(set(order)) != len(order)
    ):
        raise CanvasError(
            f'{INDEX}\'s "order" lists every board once (it has {len(boards)})'
        )
    if (
        previous is not None
        and previous.get("createdOnFiles") is not None
        and index.get("createdOnFiles") != previous.get("createdOnFiles")
    ):
        raise CanvasError(f'{INDEX}\'s "createdOnFiles" stays as it is')


def is_text(path: str) -> bool:
    return path.lower().endswith(_TEXT_TYPES)


def media_type(path: str) -> str:
    lower = path.lower()
    for ext, kind in (
        (".html", "text/html; charset=utf-8"),
        (".json", "application/json"),
        (".js", "text/javascript; charset=utf-8"),
        (".css", "text/css; charset=utf-8"),
        (".svg", "image/svg+xml"),
        (".png", "image/png"),
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".gif", "image/gif"),
        (".webp", "image/webp"),
        (".md", "text/markdown; charset=utf-8"),
        (".txt", "text/plain; charset=utf-8"),
    ):
        if lower.endswith(ext):
            return kind
    return "application/octet-stream"


def read_canvas_dir(directory) -> Dict[str, bytes]:
    """A canvas's files from a directory (as a seed or a factory's config names one)."""
    from pathlib import Path

    root = Path(directory)
    if not (root / INDEX).is_file():
        raise CanvasError(f"{root} has no {INDEX}")
    return {
        str(f.relative_to(root)): f.read_bytes()
        for f in sorted(root.rglob("*"))
        if f.is_file()
        and not any(part.startswith(".") for part in f.relative_to(root).parts)
    }
