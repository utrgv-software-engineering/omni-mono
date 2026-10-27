"""The factory's flow, as a factory engineer reads it: its runs, and what its pull requests took.

The factory's server knows its runs: what started each, how it went, what
its model calls used and cost, and the pull requests each opened or worked
on (``GET /integrations/runs``). The workplace knows the rest: who people
are, the pull requests' history, and the Slack threads work came from. This
puts the two together.

For each pull request the factory opened:

- **cost**: what every run linked to it cost (the run that opened it, and
  the ones it started later: reviews, fixes);
- **human touches**: the request, if a person made it; each message people
  posted in the request's Slack thread after it; each comment, review and
  review comment people left on the pull request; and the merge, if a person
  merged it. An automation's request counts none;
- **time to ship**: from the request to the merge.

And for the period: the totals and medians over the merged ones, and spend
sliced by source, agent, model and who asked. Over time (by day or week):

- **flow**: merged pull requests, their cost, human touches and time to
  ship, how many were later reverted (a commit on their base that reverts
  their merge), and their size (lines changed), the counter-metric beside
  cost per pull request (splitting work into tiny ones lowers it);
- **quality**: each judge's pass rate on the live runs it graded, with its
  95% interval (Wilson's): at a sample's size, a week is few runs;
- the same pass rates by **factory version** (the commit that ran), and the
  factory's **promotions** (merges into its repository's main), where a
  change went in.
"""

from __future__ import annotations

import math
import statistics
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from .server import Workplace

__all__ = ["factory_report"]


def _people(w: "Workplace") -> Dict[str, Dict[str, str]]:
    """Each person's ids in the three services, to their name."""
    slack, github, linear = {}, {}, {}
    for account in w.accounts.accounts.values():
        if account.slack_id:
            slack[account.slack_id] = account.name
        if account.github_login:
            github[account.github_login] = account.name
        if account.linear_id:
            linear[account.linear_id] = account.name
    return {"slack": slack, "github": github, "linear": linear}


def _source(
    w: "Workplace", run: Dict[str, Any], people: Dict[str, Dict[str, str]]
) -> Dict[str, Any]:
    """Where the run came from: who asked (a person, or an automation), and a link."""
    payload = run.get("payload") or {}
    provider = run["provider"]
    base = w.base_url.rstrip("/")
    who: Optional[str] = None
    link: Optional[str] = None
    thread: Optional[Tuple[str, str, str]] = (
        None  # Slack (channel, thread ts, request ts)
    )
    if provider == "slack":
        event = payload.get("event") or {}
        who = people["slack"].get(str(event.get("user") or ""))
        channel = str(
            event.get("channel") or (event.get("item") or {}).get("channel") or ""
        )
        ts = str(event.get("ts") or (event.get("item") or {}).get("ts") or "")
        if channel and ts:
            link = f"{base}/archives/{channel}/p{ts.replace('.', '')}"
            thread = (channel, str(event.get("thread_ts") or ts), ts)
    elif provider == "github":
        who = people["github"].get(str(payload.get("sender") or ""))
        item = payload.get("pull_request") or payload.get("issue") or {}
        link = item.get("html_url")
    elif provider == "linear":
        actor = payload.get("actor") or payload.get("creator") or {}
        who = (
            people["linear"].get(str(actor.get("id") or ""))
            if isinstance(actor, dict)
            else None
        )
        issue = (
            ((payload.get("agentSession") or {}).get("issue") or {})
            if isinstance(payload.get("agentSession"), dict)
            else {}
        )
        link = issue.get("url")
    return {
        "provider": provider,
        "event": run["event"],
        "person": who,
        "link": link,
        "thread": thread,
    }


def _thread_touches(
    w: "Workplace",
    thread: Optional[Tuple[str, str, str]],
    people: Dict[str, Dict[str, str]],
) -> int:
    if thread is None:
        return 0
    channel, thread_ts, request_ts = thread
    try:
        messages = w.slack.replies(channel, thread_ts)
    except Exception:
        return 0
    return sum(
        1
        for m in messages
        if m.ts != request_ts
        and m.user in people["slack"]
        and float(m.ts) > float(request_ts)
    )


def _pull_touches(pull: Any, people: Dict[str, Dict[str, str]]) -> int:
    humans = people["github"]
    touches = sum(1 for c in pull.comments if c.user in humans)
    touches += sum(
        1 for r in pull.pull.reviews if r.user in humans and r.state != "PENDING"
    )
    touches += sum(1 for t in pull.pull.threads for c in t.comments if c.user in humans)
    if pull.pull.merged and pull.pull.merged_by in humans:
        touches += 1
    return touches


def _add(
    into: Dict[str, Dict[str, Any]], key: Optional[str], row: Dict[str, Any]
) -> None:
    slot = into.setdefault(
        key or "unknown",
        {
            "requests": 0,
            "input_tokens": 0,
            "cached_tokens": 0,
            "output_tokens": 0,
            "cost": 0.0,
            "unpriced_requests": 0,
        },
    )
    slot["requests"] += int(row.get("requests") or 0)
    for field in ("input_tokens", "cached_tokens", "output_tokens"):
        slot[field] += int(row.get(field) or 0)
    if row.get("cost") is None:
        slot["unpriced_requests"] += int(row.get("requests") or 0)
    else:
        slot["cost"] += float(row["cost"])


def wilson(passed: int, graded: int, z: float = 1.96) -> Optional[Tuple[float, float]]:
    """The 95% interval of a pass rate of ``passed`` in ``graded``."""
    if graded <= 0:
        return None
    p = passed / graded
    centre = (p + z * z / (2 * graded)) / (1 + z * z / graded)
    half = (
        z
        * math.sqrt(p * (1 - p) / graded + z * z / (4 * graded * graded))
        / (1 + z * z / graded)
    )
    return max(0.0, centre - half), min(1.0, centre + half)


def _bucket_start(at: float, bucket: str) -> float:
    """The UTC day (or Monday) ``at`` falls in, in whole seconds."""
    midnight = int(at) - int(at) % 86400
    return float(
        midnight - time.gmtime(midnight).tm_wday * 86400
        if bucket == "week"
        else midnight
    )


def _reverted(repo: Any, pull: Any) -> bool:
    """A later commit on its base reverts its merge (``git revert``'s message)."""
    sha = pull.pull.merge_commit_sha
    if not sha:
        return False
    try:
        later = repo.bare.commits(f"{sha}..{pull.pull.base}")
    except Exception:
        return False
    # git revert's own message, or a revert pull request's title (in its merge's message).
    title = f'Revert "{pull.title}"'
    return any(
        f"reverts commit {sha}" in c.message or title in c.message for c in later
    )


def _size(state: Any, repo: Any, pull: Any) -> Optional[int]:
    """Lines it changes, from its base to its head."""
    try:
        if (
            pull.pull.merged
            and pull.pull.merge_commit_sha
            and pull.pull.closed_head_sha
        ):
            files = repo.bare.changes(
                f"{pull.pull.merge_commit_sha}^1", pull.pull.closed_head_sha
            )
        else:
            files = repo.bare.changes(
                state.base_sha(repo, pull), state.head_sha(repo, pull)
            )
    except Exception:
        return None
    return sum(f.changes for f in files)


def _rates(grades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for g in grades:
        if g.get("passed") is None:
            continue
        slot = out.setdefault(g["judge"], {"passed": 0, "graded": 0})
        slot["graded"] += 1
        slot["passed"] += int(bool(g["passed"]))
    for slot in out.values():
        slot["rate"] = slot["passed"] / slot["graded"]
        slot["interval"] = wilson(slot["passed"], slot["graded"])
    return out


def _promotions(
    w: "Workplace", factory_repo: Optional[str], since: float
) -> List[Dict[str, Any]]:
    repo = w.github.repos.get(factory_repo) if factory_repo else None
    if repo is None:
        return []
    merged = [
        i
        for i in repo.pulls()
        if i.pull.merged
        and i.pull.base == repo.default_branch
        and (i.pull.merged_at or 0) >= since
    ]
    return [
        {
            "number": i.number,
            "title": i.title,
            "merged_at": i.pull.merged_at,
            "commit": (i.pull.merge_commit_sha or "")[:7],
            "url": f"{w.base_url.rstrip('/')}/{repo.full_name}/pull/{i.number}",
        }
        for i in sorted(merged, key=lambda i: i.pull.merged_at or 0)
    ]


def _over_time(
    runs: List[Dict[str, Any]], pulls: List[Dict[str, Any]], bucket: str
) -> List[Dict[str, Any]]:
    buckets: Dict[float, Dict[str, Any]] = {}

    def at(t: float) -> Dict[str, Any]:
        start = _bucket_start(t, bucket)
        return buckets.setdefault(
            start, {"start": start, "spend": 0.0, "runs": 0, "grades": [], "merged": []}
        )

    for run in runs:
        slot = at(run["started_at"])
        slot["runs"] += 1
        slot["spend"] += float(run["cost"] or 0)
        slot["grades"] += run.get("grades") or []
    for pull in pulls:
        if pull["merged_at"]:
            at(pull["merged_at"])["merged"].append(pull)
    out = []
    for start in sorted(buckets):
        slot = buckets[start]
        merged = slot.pop("merged")
        costs = [p["cost"] for p in merged if p["cost"] is not None]
        sizes = [p["size"] for p in merged if p["size"] is not None]
        out.append(
            {
                "start": start,
                "runs": slot["runs"],
                "spend": slot["spend"],
                "merged": len(merged),
                "cost_per_merged_pr": (sum(costs) / len(costs)) if costs else None,
                "human_touches_per_merged_pr": (
                    statistics.mean(p["human_touches"] for p in merged)
                    if merged
                    else None
                ),
                "median_time_to_ship": (
                    statistics.median(p["time_to_ship"] for p in merged)
                    if merged
                    else None
                ),
                "reverted": sum(1 for p in merged if p["reverted"]),
                "median_size": statistics.median(sizes) if sizes else None,
                "pass_rates": _rates(slot.pop("grades")),
            }
        )
    return out


def _by_version(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order: List[str] = []
    grouped: Dict[str, Dict[str, Any]] = {}
    for run in sorted(runs, key=lambda r: r["started_at"]):
        version = run.get("version") or "unknown"
        if version not in grouped:
            order.append(version)
            grouped[version] = {
                "version": version,
                "first_run": run["started_at"],
                "runs": 0,
                "spend": 0.0,
                "grades": [],
            }
        slot = grouped[version]
        slot["runs"] += 1
        slot["spend"] += float(run["cost"] or 0)
        slot["grades"] += run.get("grades") or []
    return [
        {
            **{k: v for k, v in grouped[v].items() if k != "grades"},
            "pass_rates": _rates(grouped[v]["grades"]),
        }
        for v in order
    ]


def factory_report(
    w: "Workplace",
    runs: List[Dict[str, Any]],
    *,
    priced: bool,
    bucket: str = "day",
    factory_repo: Optional[str] = None,
    since: float = 0.0,
) -> Dict[str, Any]:
    people = _people(w)
    out_runs = []
    pulls: Dict[Tuple[str, int], Dict[str, Any]] = {}
    by = {"source": {}, "agent": {}, "model": {}, "person": {}}
    for run in runs:
        source = _source(w, run, people)
        out_runs.append(
            {
                "id": run["id"],
                "automation": run["automation"],
                "agent": run["agent"],
                "status": run["status"],
                "error": run.get("error"),
                "started_at": run["started_at"],
                "finished_at": run.get("finished_at"),
                "received_at": run.get("received_at"),
                "session_id": run["session_id"],
                "version": run.get("version"),
                "source": {k: v for k, v in source.items() if k != "thread"},
                "grades": run.get("grades") or [],
                "usage": run.get("usage") or [],
                "cost": run.get("cost"),
                "pull_requests": run.get("pull_requests") or [],
            }
        )
        for row in run.get("usage") or []:
            _add(by["source"], f"{source['provider']} {source['event']}", row)
            _add(by["agent"], row.get("agent"), row)
            _add(by["model"], row.get("model"), row)
            _add(by["person"], source["person"] or "an automation", row)
        for link in run.get("pull_requests") or []:
            key = (link["repository"], int(link["number"]))
            entry = pulls.setdefault(
                key, {"runs": [], "opened_by": None, "cost": 0.0, "cost_known": True}
            )
            entry["runs"].append(run["id"])
            if run.get("cost") is None:
                entry["cost_known"] = False
            else:
                entry["cost"] += float(run["cost"])
            if link["relation"] == "opened" and (
                entry["opened_by"] is None
                or run["started_at"] < entry["opened_by"][0]["started_at"]
            ):
                entry["opened_by"] = (run, source)
    out_pulls = []
    for (repository, number), entry in sorted(pulls.items()):
        repo = w.github.repos.get(repository)
        pull = repo.issues.get(number) if repo else None
        if pull is None or pull.pull is None or entry["opened_by"] is None:
            continue  # only the factory's own pull requests, still in the workplace
        run, source = entry["opened_by"]
        requested_at = run.get("received_at") or run["started_at"]
        touches = (
            (1 if source["person"] else 0)
            + _thread_touches(w, source["thread"], people)
            + _pull_touches(pull, people)
        )
        out_pulls.append(
            {
                "repository": repository,
                "number": number,
                "title": pull.title,
                "url": f"{w.base_url.rstrip('/')}/{repository}/pull/{number}",
                "state": "merged" if pull.pull.merged else pull.state,
                "requested_at": requested_at,
                "requested_by": source["person"],
                "merged_at": pull.pull.merged_at,
                "time_to_ship": (
                    (pull.pull.merged_at - requested_at)
                    if pull.pull.merged_at
                    else None
                ),
                "human_touches": touches,
                "runs": entry["runs"],
                "cost": entry["cost"] if entry["cost_known"] else None,
                "reverted": _reverted(repo, pull) if pull.pull.merged else False,
                "size": _size(w.github, repo, pull),
            }
        )
    merged = [p for p in out_pulls if p["state"] == "merged"]
    costs = [p["cost"] for p in merged if p["cost"] is not None]
    summary = {
        "runs": len(out_runs),
        "running": sum(1 for r in out_runs if r["status"] == "running"),
        "pull_requests": len(out_pulls),
        "merged": len(merged),
        "spend": sum(r["cost"] for r in out_runs if r["cost"] is not None),
        "unpriced_runs": sum(1 for r in out_runs if r["cost"] is None),
        "cost_per_merged_pr": (sum(costs) / len(costs)) if costs else None,
        "human_touches_per_merged_pr": (
            (statistics.mean(p["human_touches"] for p in merged)) if merged else None
        ),
        "median_time_to_ship": (
            (statistics.median(p["time_to_ship"] for p in merged)) if merged else None
        ),
    }
    summary["reverted"] = sum(1 for p in merged if p["reverted"])
    return {
        "summary": summary,
        "runs": out_runs,
        "pull_requests": out_pulls,
        "by": {
            k: dict(sorted(v.items(), key=lambda kv: -kv[1]["cost"]))
            for k, v in by.items()
        },
        "priced": priced,
        "bucket": bucket,
        "over_time": _over_time(out_runs, out_pulls, bucket),
        "by_version": _by_version(out_runs),
        "promotions": _promotions(w, factory_repo, since),
    }
