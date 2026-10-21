# Docker Development Environment

This guide covers running the Hotel Management System in a containerized development environment using Docker and Docker Compose.

## Prerequisites

- Docker (20.10+)
- Docker Compose (2.0+)
- Git

## Architecture

The development environment consists of:
- **app** - Rails 8 application with live code reloading
- **db** - PostgreSQL 17 database with persistent storage

The Solid* ecosystem (Solid Cache, Solid Queue, Solid Cable) uses PostgreSQL - no Redis needed!

## Quick Start

### 1. Build and Start Services

```bash
# Build the images and start all services
docker compose up

# Or run in detached mode (background)
docker compose up -d
```

This will:
- Build the Rails development image
- Start PostgreSQL
- Wait for database to be ready
- Run database migrations
- Start the Rails server on http://localhost:3000

### 2. First-Time Setup (Optional)

If you need to seed the database:

```bash
docker compose exec app bin/rails db:seed
```

### 3. Access the Application

Open your browser to: http://localhost:3000

## Common Commands

### Service Management

```bash
# Start services
docker compose up

# Start in background
docker compose up -d

# Stop services (preserves data)
docker compose stop

# Stop and remove containers (preserves data volumes)
docker compose down

# Stop and remove everything including volumes (⚠️ deletes database!)
docker compose down -v

# View logs
docker compose logs -f app
docker compose logs -f db

# Restart a service
docker compose restart app
```

### Rails Commands

Run any Rails command using `docker compose exec app`:

```bash
# Rails console
docker compose exec app bin/rails console

# Run migrations
docker compose exec app bin/rails db:migrate

# Rollback migration
docker compose exec app bin/rails db:rollback

# Run tests
docker compose exec app bin/rails test

# Run system tests
docker compose exec app bin/rails test:system

# Rails routes
docker compose exec app bin/rails routes

# Generate a model
docker compose exec app bin/rails generate model Article title:string

# Code quality checks
docker compose exec app bin/rubocop
docker compose exec app bin/brakeman
```

### Database Commands

```bash
# Access PostgreSQL console
docker compose exec db psql -U postgres -d hotel_management_development

# Reset database (⚠️ destroys data!)
docker compose exec app bin/rails db:reset

# Create a database backup
docker compose exec db pg_dump -U postgres hotel_management_development > backup.sql

# Restore from backup
cat backup.sql | docker compose exec -T db psql -U postgres -d hotel_management_development
```

### Shell Access

```bash
# Bash shell in the app container
docker compose exec app bash

# Or use /bin/sh if bash is not available
docker compose exec app sh

# PostgreSQL shell
docker compose exec db bash
```

### Bundle/Gem Management

```bash
# Install a new gem
# 1. Add it to Gemfile
# 2. Run bundle install
docker compose exec app bundle install

# If you added gems requiring native extensions, rebuild:
docker compose build app
docker compose up -d
```

## Development Workflow

### Live Code Reloading

Your application code is mounted as a volume, so changes to files are immediately reflected:

1. Edit files on your host machine
2. Refresh your browser to see changes
3. No rebuild needed for most changes

### When to Rebuild

You need to rebuild the Docker image when:
- Adding/removing gems in Gemfile
- Installing system packages
- Changing Dockerfile.dev

```bash
# Rebuild the app service
docker compose build app

# Rebuild and restart
docker compose up -d --build app
```

### Debugging

The app container is configured with `stdin_open` and `tty` enabled for debugging:

```bash
# Attach to running container for pry/debugger
docker attach hotel_mng_app

# Or start with attached console
docker compose up app
```

To use `binding.pry` or Rails debugger:
1. Add `binding.pry` to your code
2. Run `docker attach hotel_mng_app` in another terminal
3. Trigger the code path
4. Debugger will pause in the attached terminal

## File Permissions

The container runs as root by default. If you encounter permission issues with generated files:

```bash
# Fix ownership of generated files
docker compose exec app chown -R $(id -u):$(id -g) .
```

## Environment Variables

To customize environment variables:

1. Copy the example file:
   ```bash
   cp .env.docker.example .env.docker
   ```

2. Edit `.env.docker` with your values

3. Uncomment the `env_file` section in `docker-compose.yml`

4. Restart services:
   ```bash
   docker compose down
   docker compose up -d
   ```

## Accessing Background Services

### Solid Queue (Background Jobs)

Access the job dashboard at: http://localhost:3000/jobs

### Mailbin (Email Testing)

Access the email inbox at: http://localhost:3000/mailbin (if configured)

## Troubleshooting

### Port Already in Use

If port 3000 or 5433 is already in use, edit `docker-compose.yml`:

```yaml
services:
  app:
    ports:
      - "3001:3000"  # Use 3001 on host instead
  db:
    ports:
      - "5434:5432"  # Use 5434 on host instead
```

### Database Connection Issues

```bash
# Check if PostgreSQL is running
docker compose ps db

# View database logs
docker compose logs db

# Restart database
docker compose restart db
```

### Container Won't Start

```bash
# View detailed logs
docker compose logs app

# Remove old containers and start fresh
docker compose down
docker compose up
```

### Stale Gemfile.lock

If you have bundler version conflicts:

```bash
# Rebuild without cache
docker compose build --no-cache app
```

### Clear Everything and Start Fresh

```bash
# Stop and remove containers, networks, volumes
docker compose down -v

# Remove bundle cache
docker volume rm hotel-mng_bundle_cache

# Rebuild and start
docker compose up --build
```

## Performance Tips

### Volume Performance (macOS/Windows)

If you experience slow file I/O:

1. Use Docker Desktop's built-in caching features
2. Consider using [mutagen](https://mutagen.io/) for file sync
3. Place code on the Linux filesystem in WSL2 (Windows)

### Gem Installation Speed

Gems are cached in a named volume (`bundle_cache`) for faster rebuilds. This cache persists between container recreations.

## Connecting to External Postgres (Your postgres17 container)

If you want to use your existing `postgres17` container instead:

1. Edit `docker-compose.yml` and change the app's `DATABASE_HOST`:
   ```yaml
   environment:
     DATABASE_HOST: host.docker.internal
   ```

2. Remove or comment out the `db` service

3. Create the databases in your `postgres17` container:
   ```bash
   docker exec postgres17 createdb -U postgres hotel_management_development
   docker exec postgres17 createdb -U postgres hotel_management_test
   ```

## Production vs Development

This Docker setup is **for development only**. Key differences from production:

| Feature | Development | Production |
|---------|-------------|------------|
| Code | Volume mounted (live reload) | Copied into image |
| Assets | Compiled on-demand | Precompiled |
| Gems | Includes dev/test gems | Production only |
| User | Root (easy file access) | Non-root (security) |
| Image size | Larger (build tools) | Smaller (multi-stage) |
| Database auth | Trust (no password) | Password required |

## Next Steps

- See [CLAUDE.md](CLAUDE.md) for project architecture overview
- See [README.md](README.md) for application setup and features
- Configure Stripe keys in `.env.docker` for payment testing
- Set up multi-tenant subdomains (requires DNS configuration)

## Getting Help

If you encounter issues:

1. Check the logs: `docker compose logs -f`
2. Verify services are running: `docker compose ps`
3. Try rebuilding: `docker compose build --no-cache`
4. Use lazydocker for visual monitoring

## Useful LazyDocker Views

Since you have lazydocker installed:

```bash
# Launch lazydocker to monitor containers
lazydocker
```

In lazydocker you can:
- View real-time logs for all services
- Restart/stop individual containers
- Inspect container stats (CPU, memory)
- Execute commands in containers
- View container configurations
