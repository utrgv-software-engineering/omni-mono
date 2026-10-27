# api-service

Acme's task tracker: a small JSON API and a web page listing tasks.

```bash
python -m app.serve --port 8000     # http://localhost:8000
python -m unittest discover -s tests
```

## API

- `GET /tasks`: every task, newest first.
- `GET /tasks/<id>`: one task, or 404.
- `POST /tasks` with `{"title": "..."}`: create a task; returns it with 201.

See [docs/api.md](docs/api.md) for the response shapes.
