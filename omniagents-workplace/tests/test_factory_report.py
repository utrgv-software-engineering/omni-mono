"""The factory's flow, from its runs and the workplace's history."""

import time

from omniagents_workplace.factory_report import factory_report
from omniagents_workplace.server import Workplace, load_seed


def test_a_pull_requests_cost_touches_and_time_to_ship(tmp_path):
    w = Workplace(tmp_path / "data", base_url="http://w", seed=load_seed())
    try:
        priya = w.accounts.by_username("priya")
        dana = w.accounts.by_username("dana")
        eng = next(c for c in w.slack.channels.values() if c.name == "eng")
        request = w.slack.post_message(
            priya.slack_id, eng.id, "<@factory> add GET /tasks/{id}"
        )
        requested_at = time.time()
        gh = w.github
        repo = gh.repos["acme/api-service"]
        gh.create_branch(repo, "acme-factory", "factory/3", "main")
        gh.write_files(repo, "acme-factory", "factory/3", [("docs/x.md", b"x\n")], "x")
        pull = gh.create_pull(
            repo,
            "acme-factory",
            "Add GET /tasks/{id}",
            "factory/3",
            "main",
            "Closes #3.",
        )
        w.slack.post_message(
            priya.slack_id,
            eng.id,
            "Also a 404 for a missing one, please",
            thread_ts=request.ts,
        )
        w.slack.post_message(
            w.apps.by_username("acme-factory").slack_user_id,
            eng.id,
            "On it",
            thread_ts=request.ts,
        )
        gh.create_review(repo, pull, priya.github_login, event="APPROVE")
        gh.merge_pull(repo, pull, dana.github_login, bypass_protection=True)

        def run(id, agent, provider, event, payload, cost, links, received_at):
            return {
                "id": id,
                "automation": "a",
                "agent": agent,
                "status": "completed",
                "session_id": f"S{id}",
                "started_at": received_at + 1,
                "finished_at": received_at + 60,
                "received_at": received_at,
                "provider": provider,
                "event": event,
                "payload": payload,
                "grades": [],
                "usage": [
                    {
                        "agent": agent,
                        "model": "gpt-6-astra",
                        "requests": 3,
                        "input_tokens": 1000,
                        "cached_tokens": 900,
                        "output_tokens": 50,
                        "cost": cost,
                    }
                ],
                "cost": cost,
                "pull_requests": links,
            }

        opened = [
            {
                "repository": "acme/api-service",
                "number": pull.number,
                "relation": "opened",
            }
        ]
        worked = [
            {
                "repository": "acme/api-service",
                "number": pull.number,
                "relation": "worked",
            }
        ]
        runs = [
            run(
                1,
                "foreman",
                "slack",
                "app_mention",
                {
                    "event": {
                        "user": priya.slack_id,
                        "channel": eng.id,
                        "ts": request.ts,
                    }
                },
                1.5,
                opened,
                requested_at,
            ),
            run(
                2,
                "code_reviewer",
                "github",
                "pull_request_opened",
                {"sender": "acme-factory", "pull_request": {"number": pull.number}},
                0.5,
                worked,
                requested_at + 5,
            ),
            run(
                3,
                "improver",
                "schedule",
                "cron_fired",
                {"schedule": "nightly-review"},
                0.25,
                [],
                requested_at + 9,
            ),
        ]
        report = factory_report(w, runs, priced=True)
        [pr] = report["pull_requests"]
        assert pr["state"] == "merged" and pr["requested_by"] == priya.name
        assert pr["cost"] == 2.0 and pr["runs"] == [1, 2]
        # The request, her follow-up in its thread (not the factory's), her approval and Dana's merge.
        assert pr["human_touches"] == 4
        assert 0 < pr["time_to_ship"] < 60
        summary = report["summary"]
        assert (
            summary["merged"],
            summary["cost_per_merged_pr"],
            summary["human_touches_per_merged_pr"],
        ) == (1, 2.0, 4)
        assert summary["spend"] == 2.25
        assert report["by"]["person"][priya.name]["cost"] == 1.5
        assert report["by"]["person"]["an automation"]["cost"] == 0.75
        assert (
            report["runs"][0]["source"]["link"]
            == f"http://w/archives/{eng.id}/p{request.ts.replace('.', '')}"
        )
        assert pr["size"] == 1 and pr["reverted"] is False

        # Reverted by a later pull request: it counts against the period's merges.
        gh.create_branch(repo, "dana", "revert-3", "main")
        gh.write_files(repo, "dana", "revert-3", [("docs/x.md", None)], "Revert it")
        revert = gh.create_pull(
            repo, "dana", f'Revert "{pull.title}"', "revert-3", "main", ""
        )
        gh.merge_pull(repo, revert, dana.github_login, bypass_protection=True)
        again = factory_report(w, runs, priced=True)
        assert (
            again["pull_requests"][0]["reverted"] is True
            and again["summary"]["reverted"] == 1
        )
    finally:
        w.close()


def test_quality_and_flow_over_time_and_by_version(tmp_path):
    from omniagents_workplace.factory_report import wilson

    w = Workplace(tmp_path / "data", base_url="http://w", seed=load_seed())
    try:
        day = 86400.0
        start = 1_800_000_000.0  # a Tuesday, 03:00 UTC... the buckets are UTC days

        def run(i, at, version, grades, cost):
            return {
                "id": i,
                "automation": "a",
                "agent": "foreman",
                "status": "completed",
                "session_id": f"S{i}",
                "started_at": at,
                "finished_at": at + 60,
                "received_at": at,
                "provider": "schedule",
                "event": "cron_fired",
                "payload": {},
                "grades": grades,
                "usage": [],
                "cost": cost,
                "pull_requests": [],
                "version": version,
            }

        g = lambda passed: {
            "judge": "task_compliance",
            "agent": "implementer",
            "passed": passed,
        }
        runs = [
            run(1, start + 0.25, "aaa111", [g(True), g(False)], 1.0),
            run(
                2, start + 60.75, "aaa111", [g(False)], 2.0
            ),  # the same day, at another fraction of a second
            run(3, start + day, "bbb222", [g(True), g(True)], 0.5),
        ]
        report = factory_report(w, runs, priced=True, bucket="day")
        first, second = report["over_time"]
        assert second["start"] - first["start"] == day
        assert (first["runs"], first["spend"]) == (2, 3.0)
        assert first["pass_rates"]["task_compliance"]["passed"] == 1
        assert first["pass_rates"]["task_compliance"]["graded"] == 3
        low, high = first["pass_rates"]["task_compliance"]["interval"]
        assert low < 1 / 3 < high
        assert [v["version"] for v in report["by_version"]] == ["aaa111", "bbb222"]
        assert report["by_version"][1]["pass_rates"]["task_compliance"]["rate"] == 1.0
        assert wilson(0, 0) is None
    finally:
        w.close()


def test_the_factorys_promotions_mark_where_changes_went_in(tmp_path):
    w = Workplace(tmp_path / "data", base_url="http://w", seed=load_seed())
    try:
        gh = w.github
        repo = gh.create_repo("acme", "factory", "admin", auto_init=True)
        gh.create_branch(repo, "acme-factory", "improve/1", "main")
        gh.write_files(
            repo,
            "acme-factory",
            "improve/1",
            [("agents/x.md", b"x\n")],
            "Better prompt",
        )
        pull = gh.create_pull(
            repo, "acme-factory", "Review drafts once ready", "improve/1", "main", ""
        )
        gh.merge_pull(repo, pull, "dana", bypass_protection=True)
        [promotion] = factory_report(w, [], priced=True, factory_repo="acme/factory")[
            "promotions"
        ]
        assert (
            promotion["title"] == "Review drafts once ready"
            and len(promotion["commit"]) == 7
        )
        assert (
            factory_report(
                w,
                [],
                priced=True,
                factory_repo="acme/factory",
                since=promotion["merged_at"] + 1,
            )["promotions"]
            == []
        )
    finally:
        w.close()
