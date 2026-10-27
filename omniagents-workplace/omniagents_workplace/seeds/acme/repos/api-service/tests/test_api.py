import json
import unittest

from app import api
from app.store import seeded


class ListTasksTest(unittest.TestCase):
    def test_list_returns_every_task_newest_first(self):
        status, body = api.list_tasks(seeded(5), {})
        self.assertEqual(status, 200)
        self.assertEqual([t["id"] for t in body["tasks"]], [5, 4, 3, 2, 1])


class GetTaskTest(unittest.TestCase):
    def test_get_existing_task(self):
        status, body = api.get_task(seeded(3), "2")
        self.assertEqual((status, body["title"]), (200, "Task 2"))

    def test_get_missing_task_is_404(self):
        self.assertEqual(api.get_task(seeded(3), "99")[0], 404)

    def test_get_non_numeric_id_is_404(self):
        self.assertEqual(api.get_task(seeded(3), "abc")[0], 404)


class CreateTaskTest(unittest.TestCase):
    def test_create_returns_201_with_the_task(self):
        status, body = api.create_task(
            seeded(0), json.dumps({"title": " Write docs "}).encode()
        )
        self.assertEqual((status, body["title"]), (201, "Write docs"))

    def test_create_rejects_empty_title(self):
        self.assertEqual(api.create_task(seeded(0), b'{"title": "  "}')[0], 400)

    def test_create_rejects_invalid_json(self):
        self.assertEqual(api.create_task(seeded(0), b"{not json")[0], 400)


if __name__ == "__main__":
    unittest.main()
