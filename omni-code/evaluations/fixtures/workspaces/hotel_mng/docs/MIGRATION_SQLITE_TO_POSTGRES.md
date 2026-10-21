# Migration Guide: SQLite to PostgreSQL on Azure

This guide covers migrating the Hotel Management System from SQLite to PostgreSQL using Azure App Service's sidecar feature.

## Overview

**Why migrate?**
- SQLite with Azure's network-mounted storage causes corruption under concurrent load
- PostgreSQL handles concurrent connections properly
- Better suited for production workloads

**Architecture Change:**
```
Before: Rails App → SQLite (on Azure persistent storage)
After:  Rails App → PostgreSQL Sidecar (on localhost, data in /home/postgres/data)
```

## Prerequisites

1. Azure CLI installed and logged in (`az login`)
2. SSH access to current production container (`python rocketship.py ssh`)
3. `.env` file with `POSTGRES_PASSWORD` set

## Migration Steps

### Phase 1: Backup Current Data

1. **SSH into the current production container:**
   ```bash
   python rocketship.py ssh
   ```

2. **Run the backup export task** (automatically saves compressed archive to /home/backups):
   ```bash
   cd /rails
   RAILS_ENV=production bin/rails db:backup:export
   ```

   This creates a `.tar.gz` archive in `/home/backups/` (Azure persistent storage).

3. **Download the backup locally** (from your local machine):
   ```bash
   # List available backups
   python rocketship.py download

   # Download the latest backup
   python rocketship.py download backup-20241218_120000.tar.gz
   ```

### Phase 2: Update Environment Variables

Add to your `.env` file:
```bash
# PostgreSQL password (generate a secure one)
POSTGRES_PASSWORD=your-secure-password-here
```

### Phase 3: Deploy PostgreSQL Sidecar

1. **Build and push the updated Docker image:**
   ```bash
   python rocketship.py deploy
   ```

   This will:
   - Build the new image with PostgreSQL client libraries
   - Enable sidecar mode on Azure App Service
   - Configure the PostgreSQL sidecar container
   - Update environment variables including DATABASE_URL
   - Restart the application

2. **Verify the deployment:**
   ```bash
   python rocketship.py logs
   ```

   Look for:
   - "Waiting for PostgreSQL to be ready..."
   - "PostgreSQL is ready!"
   - "Preparing databases..."

### Phase 4: Import Data

1. **Upload the backup to the new container** (if needed):
   ```bash
   # Upload local backup to Azure
   python rocketship.py upload backup-20241218_120000.tar.gz
   ```

2. **SSH into the new container and run import:**
   ```bash
   python rocketship.py ssh
   ```

   ```bash
   # List available backups
   RAILS_ENV=production bin/rails db:backup:list

   # Import the latest backup (auto-detected)
   RAILS_ENV=production bin/rails db:backup:import

   # Or specify a specific backup file
   RAILS_ENV=production BACKUP_FILE=backup-20241218_120000.tar.gz bin/rails db:backup:import
   ```

3. **Verify the import:**
   ```bash
   RAILS_ENV=production bin/rails console
   ```

   ```ruby
   # Check record counts
   puts "Accounts: #{Account.count}"
   puts "Users: #{User.count}"
   puts "Hotels: #{Hotel.count}"
   puts "Reservations: #{Reservation.count}"
   ```

### Phase 5: Verify Application

1. **Test the application:**
   - Log in as an existing user
   - Check reservations are visible
   - Create a test reservation
   - Verify it persists after restart

2. **Restart and verify persistence:**
   ```bash
   python rocketship.py restart
   # Wait for restart to complete
   python rocketship.py ssh
   ```

   ```bash
   RAILS_ENV=production bin/rails console
   ```

   ```ruby
   # Verify data is still there
   puts Reservation.last.confirmation_number
   ```

## Rollback Plan

If migration fails, you can rollback:

1. **Revert config/database.yml** to use SQLite
2. **Revert Dockerfile** to use sqlite3 libraries
3. **Remove sidecars config** from config/azure-deploy.yml
4. **Deploy the rollback:**
   ```bash
   git checkout HEAD~1 -- config/database.yml Dockerfile config/azure-deploy.yml
   python rocketship.py deploy
   ```

## Troubleshooting

### PostgreSQL won't start
Check the sidecar logs:
```bash
az webapp log tail --name $AZURE_APP_NAME --resource-group $AZURE_RESOURCE_GROUP
```

Common issues:
- `/home/postgres/data` permissions
- PGDATA directory already exists but corrupted

### Connection refused
The app starts before PostgreSQL is ready. The entrypoint waits up to 60 seconds. If still failing:
```bash
# Increase WEBSITES_CONTAINER_START_TIME_LIMIT in Azure
az webapp config appsettings set --name $AZURE_APP_NAME --resource-group $AZURE_RESOURCE_GROUP \
  --settings WEBSITES_CONTAINER_START_TIME_LIMIT=900
```

### Import fails
Check the manifest for errors:
```bash
cat db/backup/*/manifest.json
```

Try importing tables individually:
```bash
RAILS_ENV=production bin/rails runner "
  require 'json'
  data = JSON.parse(File.read('db/backup/TIMESTAMP/accounts.json'))
  data.each { |r| Account.create!(r) rescue puts \"Skip: #{r['id']}\" }
"
```

## Configuration Reference

### Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `POSTGRES_USER` | PostgreSQL user (default: hotel) |
| `PGDATA` | PostgreSQL data directory |

### Files Changed

| File | Change |
|------|--------|
| `Gemfile` | Added `pg` gem |
| `config/database.yml` | PostgreSQL config for production |
| `config/azure-deploy.yml` | Sidecar configuration |
| `Dockerfile` | PostgreSQL client libraries |
| `bin/docker-entrypoint` | Wait for PostgreSQL |
| `lib/tasks/backup.rake` | Export/import tasks |
| `rocketship.py` | Sidecar deployment functions |

## Sources

- [Azure App Service Sidecars - Deep Dive](https://azure.github.io/AppService/2025/03/06/Sidecars-Deep-Dive-Part1.html)
- [Docker Compose Migration to Sidecars](https://azure.github.io/AppService/2025/04/01/Docker-compose-migration.html)
- [Configure Sidecars - Microsoft Docs](https://learn.microsoft.com/en-us/azure/app-service/configure-sidecar)
- [Sidecars GA Announcement](https://azure.github.io/AppService/2024/11/08/Global-Availability-Sidecars.html)
