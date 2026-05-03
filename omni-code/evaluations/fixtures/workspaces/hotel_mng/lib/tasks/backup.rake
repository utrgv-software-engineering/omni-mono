# frozen_string_literal: true

require "json"
require "fileutils"
require "zlib"
require "rubygems/package"

namespace :db do
  namespace :backup do
    # Default backup location - uses Azure persistent storage in production
    def default_backup_dir
      if Rails.env.production? && ENV["WEBSITES_ENABLE_APP_SERVICE_STORAGE"]
        "/home/backups"
      else
        Rails.root.join("db/backup").to_s
      end
    end

    # Tables to export/import in dependency order
    TABLES = %w[
      accounts
      users
      roles
      account_users
      account_users_roles
      amenities
      hotels
      hotel_policies
      cancellation_policies
      hotel_ai_settings
      hotel_amenities
      room_types
      room_type_amenities
      rooms
      guests
      services
      service_schedules
      reservations
      service_bookings
      housekeeping_tasks
      maintenance_requests
      chat_sessions
      chat_messages
      blazer_queries
      blazer_dashboards
      blazer_dashboard_queries
      blazer_checks
      blazer_audits
    ].freeze

    desc "Export all data to compressed archive (saves to /home/backups in production)"
    task export: :environment do
      backup_dir = ENV.fetch("BACKUP_DIR", default_backup_dir)
      FileUtils.mkdir_p(backup_dir)

      timestamp = Time.current.strftime("%Y%m%d_%H%M%S")
      archive_name = "backup-#{timestamp}.tar.gz"
      archive_path = File.join(backup_dir, archive_name)

      puts "=" * 60
      puts "Database Export"
      puts "=" * 60
      puts "Creating: #{archive_path}"
      puts

      manifest = {
        exported_at: Time.current.iso8601,
        rails_env: Rails.env,
        schema_version: ActiveRecord::Migrator.current_version,
        tables: {}
      }

      # Collect all data first, then write archive
      table_data = {}

      TABLES.each do |table_name|
        begin
          unless ActiveRecord::Base.connection.table_exists?(table_name)
            puts "  [SKIP] #{table_name} - table does not exist"
            next
          end

          records = ActiveRecord::Base.connection.select_all(
            "SELECT * FROM #{table_name}"
          ).to_a

          table_data[table_name] = records
          manifest[:tables][table_name] = { count: records.count }
          puts "  [OK] #{table_name}: #{records.count} records"
        rescue => e
          puts "  [ERROR] #{table_name}: #{e.message}"
          manifest[:tables][table_name] = { error: e.message }
        end
      end

      # Write tar.gz archive
      File.open(archive_path, "wb") do |file|
        Zlib::GzipWriter.wrap(file) do |gzip|
          Gem::Package::TarWriter.new(gzip) do |tar|
            # Write table data
            table_data.each do |table_name, records|
              json_content = JSON.pretty_generate(records)
              tar.add_file_simple("#{table_name}.json", 0o644, json_content.bytesize) do |io|
                io.write(json_content)
              end
            end

            # Write manifest
            manifest_json = JSON.pretty_generate(manifest)
            tar.add_file_simple("manifest.json", 0o644, manifest_json.bytesize) do |io|
              io.write(manifest_json)
            end
          end
        end
      end

      file_size = File.size(archive_path)
      puts
      puts "=" * 60
      puts "Export complete!"
      puts "Archive: #{archive_path}"
      puts "Size: #{(file_size / 1024.0).round(2)} KB"
      puts
      puts "To download locally, run:"
      puts "  python rocketship.py download #{archive_name}"
      puts "=" * 60
    end

    desc "List available backups in persistent storage"
    task list: :environment do
      backup_dir = ENV.fetch("BACKUP_DIR", default_backup_dir)

      puts "=" * 60
      puts "Available Backups"
      puts "=" * 60
      puts "Location: #{backup_dir}"
      puts

      unless Dir.exist?(backup_dir)
        puts "No backups found (directory does not exist)"
        exit 0
      end

      backups = Dir.glob(File.join(backup_dir, "backup-*.tar.gz")).sort.reverse

      if backups.empty?
        puts "No backups found"
      else
        backups.each do |backup|
          name = File.basename(backup)
          size = File.size(backup)
          mtime = File.mtime(backup)
          puts "  #{name}  (#{(size / 1024.0).round(2)} KB)  #{mtime.strftime('%Y-%m-%d %H:%M:%S')}"
        end
      end
      puts
    end

    desc "Import data from backup archive (auto-detects latest if no BACKUP_FILE specified)"
    task import: :environment do
      backup_dir = ENV.fetch("BACKUP_DIR", default_backup_dir)
      backup_file = ENV.fetch("BACKUP_FILE", nil)

      # Find the backup file
      if backup_file
        archive_path = if File.exist?(backup_file)
          backup_file
        else
          File.join(backup_dir, backup_file)
        end
      else
        # Find the latest backup
        backups = Dir.glob(File.join(backup_dir, "backup-*.tar.gz")).sort
        if backups.empty?
          puts "Error: No backups found in #{backup_dir}"
          puts "Specify a file with: BACKUP_FILE=backup-20241218_120000.tar.gz bin/rails db:backup:import"
          exit 1
        end
        archive_path = backups.last
      end

      unless File.exist?(archive_path)
        puts "Error: Backup file not found: #{archive_path}"
        exit 1
      end

      puts "=" * 60
      puts "Database Import"
      puts "=" * 60
      puts "Importing from: #{archive_path}"
      puts

      # Extract and read manifest first
      manifest = nil
      table_data = {}

      File.open(archive_path, "rb") do |file|
        Zlib::GzipReader.wrap(file) do |gzip|
          Gem::Package::TarReader.new(gzip) do |tar|
            tar.each do |entry|
              next unless entry.file?

              content = entry.read
              if entry.full_name == "manifest.json"
                manifest = JSON.parse(content)
              elsif entry.full_name.end_with?(".json")
                table_name = File.basename(entry.full_name, ".json")
                table_data[table_name] = JSON.parse(content)
              end
            end
          end
        end
      end

      unless manifest
        puts "Error: No manifest.json found in archive"
        exit 1
      end

      puts "Original export: #{manifest['exported_at']}"
      puts "Schema version: #{manifest['schema_version']}"
      puts

      # Check if we're on PostgreSQL
      adapter = ActiveRecord::Base.connection.adapter_name.downcase
      is_postgres = adapter.include?("postgresql")

      # Disable foreign key checks during import
      if is_postgres
        ActiveRecord::Base.connection.execute("SET session_replication_role = 'replica';")
      else
        ActiveRecord::Base.connection.execute("PRAGMA foreign_keys = OFF;")
      end

      TABLES.each do |table_name|
        records = table_data[table_name]
        next unless records

        begin
          if records.empty?
            puts "  [SKIP] #{table_name}: 0 records"
            next
          end

          unless ActiveRecord::Base.connection.table_exists?(table_name)
            puts "  [SKIP] #{table_name} - table does not exist"
            next
          end

          # Clear existing data
          ActiveRecord::Base.connection.execute("DELETE FROM #{table_name}")

          # Insert records
          records.each do |record|
            columns = record.keys
            placeholders = if is_postgres
              columns.map.with_index { |_, i| "$#{i + 1}" }.join(", ")
            else
              columns.map { "?" }.join(", ")
            end

            sql = "INSERT INTO #{table_name} (#{columns.join(', ')}) VALUES (#{placeholders})"

            if is_postgres
              ActiveRecord::Base.connection.exec_query(
                sql, "SQL",
                record.values.map { |v| [nil, v] }
              )
            else
              ActiveRecord::Base.connection.exec_query(
                sql, "SQL",
                record.values.map { |v| [nil, v] }
              )
            end
          end

          # Reset sequence for PostgreSQL
          if is_postgres && ActiveRecord::Base.connection.column_exists?(table_name, :id)
            max_id = ActiveRecord::Base.connection.select_value("SELECT MAX(id) FROM #{table_name}") || 0
            begin
              ActiveRecord::Base.connection.execute(
                "SELECT setval(pg_get_serial_sequence('#{table_name}', 'id'), #{max_id + 1}, false)"
              )
            rescue => e
              # Some tables might not have sequences
            end
          end

          puts "  [OK] #{table_name}: #{records.count} records"
        rescue => e
          puts "  [ERROR] #{table_name}: #{e.message}"
        end
      end

      # Re-enable foreign key checks
      if is_postgres
        ActiveRecord::Base.connection.execute("SET session_replication_role = 'origin';")
      else
        ActiveRecord::Base.connection.execute("PRAGMA foreign_keys = ON;")
      end

      puts
      puts "=" * 60
      puts "Import complete!"
      puts "=" * 60
    end
  end
end
