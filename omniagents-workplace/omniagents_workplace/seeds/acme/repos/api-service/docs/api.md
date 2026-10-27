# API reference

## GET /tasks

Returns `{"tasks": [Task, ...]}`, newest first.

## GET /tasks/<id>

Returns a `Task`, or `{"error": "not found"}` with 404.

## POST /tasks

Body: `{"title": "<1-200 characters>"}`. Returns the new `Task` with 201,
or `{"error": "<message>"}` with 400.

## Task

`{"id": int, "title": str, "done": bool}`
