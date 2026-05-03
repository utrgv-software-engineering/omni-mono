# frozen_string_literal: true

# Migration to convert PostgreSQL jsonb columns to json for SQLite compatibility.
# SQLite supports JSON via the json column type, which Rails handles transparently.
# This migration is safe to run - it recreates the columns with the same data.
class ConvertJsonbToJsonForSqlite < ActiveRecord::Migration[8.0]
  def up
    # For SQLite, json columns work the same way - data is stored as TEXT
    # and Rails handles serialization/deserialization automatically.
    #
    # Since we're switching from PostgreSQL to SQLite, we need to recreate
    # the schema. The jsonb -> json change is handled by the new schema.rb
    # that will be generated when running db:schema:load on SQLite.
    #
    # This migration serves as documentation and ensures the schema
    # regeneration happens correctly.

    # chat_messages.metadata - change from jsonb to json
    if column_exists?(:chat_messages, :metadata)
      # SQLite doesn't have a jsonb type, so we change to json (stored as TEXT)
      change_column :chat_messages, :metadata, :json
    end

    # hotel_ai_settings.settings - change from jsonb to json
    if column_exists?(:hotel_ai_settings, :settings)
      change_column :hotel_ai_settings, :settings, :json, default: {}
    end
  end

  def down
    # Reverting to jsonb would only work on PostgreSQL
    # On SQLite, json columns remain as json (TEXT storage)
    if ActiveRecord::Base.connection.adapter_name == "PostgreSQL"
      change_column :chat_messages, :metadata, :jsonb if column_exists?(:chat_messages, :metadata)
      change_column :hotel_ai_settings, :settings, :jsonb, default: {} if column_exists?(:hotel_ai_settings, :settings)
    end
  end
end
