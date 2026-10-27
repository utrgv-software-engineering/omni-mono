import unittest

from app import web
from app.store import Task


class RenderTaskListTest(unittest.TestCase):
    def test_lists_titles_escaped(self):
        page = web.render_task_list([Task(id=1, title="<b>x</b>")])
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)

    def test_marks_done_tasks(self):
        page = web.render_task_list([Task(id=1, title="a", done=True)])
        self.assertIn('class="task done"', page)


if __name__ == "__main__":
    unittest.main()
