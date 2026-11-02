"""One durable preview-status comment per factory/provider/repository/PR."""

import asyncio
import hashlib
import logging

import httpx

KIND = "deployment.preview.comment"
log = logging.getLogger(__name__)


def body_for(marker, records):
    lines = [marker, "**Factory previews**", ""]
    for record in sorted(records, key=lambda r: r["application"]):
        state = record["state"]
        status = record.get("build_status", "queued")
        if state == "closed":
            label = "Removed"
        elif state == "closing":
            label = "Removing"
        elif state == "error" or status == "failed":
            label = "Failed"
        elif status in {"cancelled", "cancelled-by-user"}:
            label = "Cancelled"
        elif status == "finished":
            label = "Ready"
        elif status == "in_progress":
            label = "Building"
        else:
            label = "Queued"
        line = f"- {label} · `{record['sha'][:12]}`"
        if label == "Ready":
            line += " · " + " · ".join(
                f"[Open preview]({url})" for url in record["urls"]
            )
        lines.append(line)
    return "\n".join(lines)


async def publish(previews):
    w = previews.i.instances[previews.factory_id][0]
    provider = w.repository_settings.settings()["provider"]
    groups = {}
    for record in previews.records().values():
        # Never send a stand-in PR number to real GitHub (or vice versa).
        if record["provider"] == provider:
            groups.setdefault((record["repository"], record["number"]), []).append(
                record
            )
    for (repo, number), records in groups.items():
        key = hashlib.sha256(
            f"{previews.factory_id}:{provider}:{repo}:{number}".encode()
        ).hexdigest()
        saved = dict(dict(previews.store.docs(KIND)).get(key) or {})
        if not saved and all(r["state"] == "closed" for r in records):
            continue  # Do not add comments to historical, already-removed previews.
        marker = f"<!-- workplace-preview:{key} -->"
        body = body_for(marker, records)
        if saved.get("body") == body:
            continue
        try:
            if provider == "standin":
                r = w.github.repos[repo]
                issue = r.issues[number]
                actor = w.github.add_user("workplace-previews[bot]", type="Bot").login
                existing = next(
                    (
                        c
                        for c in issue.comments
                        if c.user == actor and c.body.startswith(marker)
                    ),
                    None,
                )
                if existing:
                    if existing.body != body:
                        w.github.edit_comment(r, existing.id, actor, body)
                    comment_id = existing.id
                else:
                    comment_id = w.github.add_comment(r, issue, actor, body).id
            else:
                token = await asyncio.to_thread(w.repository_settings.token)
                async with httpx.AsyncClient(
                    base_url="https://api.github.com",
                    timeout=20,
                    headers={
                        "Authorization": "Bearer " + token,
                        "Accept": "application/vnd.github+json",
                    },
                ) as client:

                    async def request(method, path, **kwargs):
                        response = await client.request(method, path, **kwargs)
                        response.raise_for_status()
                        return response.json()

                    actor = (await request("GET", "/user"))["id"]
                    existing = None
                    page = 1
                    while True:
                        batch = await request(
                            "GET",
                            f"/repos/{repo}/issues/{number}/comments?per_page=100&page={page}",
                        )
                        existing = next(
                            (
                                c
                                for c in batch
                                if c["user"]["id"] == actor
                                and c.get("body", "").startswith(marker)
                            ),
                            None,
                        )
                        if existing or len(batch) < 100:
                            break
                        page += 1
                    if existing:
                        comment_id = existing["id"]
                        if existing["body"] != body:
                            await request(
                                "PATCH",
                                f"/repos/{repo}/issues/comments/{comment_id}",
                                json={"body": body},
                            )
                    else:
                        comment_id = (
                            await request(
                                "POST",
                                f"/repos/{repo}/issues/{number}/comments",
                                json={"body": body},
                            )
                        )["id"]
            saved.update(body=body, comment_id=comment_id, error=None)
        except Exception as exc:
            # A comment permission/outage must never interrupt builds or cleanup.
            # Do not persist response bodies, headers, or tokens.
            saved["error"] = f"Preview comment update failed ({type(exc).__name__})"
            log.warning(
                "Factory %s PR %s#%s: %s",
                previews.factory_id,
                repo,
                number,
                saved["error"],
            )
        previews.store.put(KIND, key, saved)
