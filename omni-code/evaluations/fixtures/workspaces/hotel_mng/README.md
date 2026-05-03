# README

This README would normally document whatever steps are necessary to get the
application up and running.

Things you may want to cover:

* Ruby version

* System dependencies

* Configuration

* Database creation

* Database initialization

* How to run the test suite

* Services (job queues, cache servers, search engines, etc.)

* Deployment instructions

* ...

## Docker development

- Copy `.env.docker.example` to `.env.docker` if you need to set custom Rails env vars and create `agents/.env` with `HOTEL_API_KEY` and `HOTEL_ID` so the AI agents can authenticate.
- Run `docker compose up --build` to build `Dockerfile.dev` and start the stack; the container executes `bin/dev`, so Rails, Tailwind, and both AI agents boot together just like the local workflow. Each worktree gets unique container names automatically.
- The entrypoint bootstraps a container-specific Python environment in `/var/lib/hotel-agents-venv` (stored on a Docker named volume, not your host). If you change `agents/requirements.txt`, run `docker compose down -v` and `docker compose up` to refresh it.
- Override the agents virtualenv path by setting `AGENTS_VENV` if you need something besides the default `.venv` (host) or `.venv-docker` (Docker).
