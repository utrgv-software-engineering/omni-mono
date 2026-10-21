# This file is auto-generated from the current state of the database. Instead
# of editing this file, please use the migrations feature of Active Record to
# incrementally modify your database, and then regenerate this schema definition.
#
# This file is the source Rails uses to define your schema when running `bin/rails
# db:schema:load`. When creating a new database, `bin/rails db:schema:load` tends to
# be faster and is potentially less error prone than running all of your
# migrations from scratch. Old migrations may fail to apply correctly if those
# migrations use external dependencies or application code.
#
# It's strongly recommended that you check this file into your version control system.

ActiveRecord::Schema[8.0].define(version: 2025_12_02_171402) do
  create_table "account_users", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "user_id", null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.string "api_key"
    t.datetime "api_key_last_used_at"
    t.string "generated_password"
    t.index ["account_id"], name: "index_account_users_on_account_id"
    t.index ["api_key"], name: "index_account_users_on_api_key", unique: true
    t.index ["user_id"], name: "index_account_users_on_user_id"
  end

  create_table "account_users_roles", id: false, force: :cascade do |t|
    t.bigint "account_user_id"
    t.bigint "role_id"
    t.index ["account_user_id", "role_id"], name: "index_account_users_roles_on_account_user_id_and_role_id"
    t.index ["account_user_id"], name: "index_account_users_roles_on_account_user_id"
    t.index ["role_id"], name: "index_account_users_roles_on_role_id"
  end

  create_table "accounts", force: :cascade do |t|
    t.string "name"
    t.string "subdomain"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.string "address"
    t.string "city"
    t.string "state"
    t.string "country"
    t.string "postal_code"
    t.string "phone"
    t.string "email"
    t.string "timezone", default: "UTC"
    t.string "currency", default: "USD"
    t.string "status", default: "active"
    t.datetime "trial_ends_at"
    t.index ["subdomain"], name: "index_accounts_on_subdomain", unique: true
  end

  create_table "amenities", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.string "name", null: false
    t.string "category", null: false
    t.string "icon"
    t.text "description"
    t.boolean "is_countable", default: false, null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "category"], name: "index_amenities_on_account_id_and_category"
    t.index ["account_id", "name"], name: "index_amenities_on_account_id_and_name", unique: true
    t.index ["account_id"], name: "index_amenities_on_account_id"
    t.index ["category"], name: "index_amenities_on_category"
  end

  create_table "blazer_audits", force: :cascade do |t|
    t.bigint "user_id"
    t.bigint "query_id"
    t.text "statement"
    t.string "data_source"
    t.datetime "created_at"
    t.index ["query_id"], name: "index_blazer_audits_on_query_id"
    t.index ["user_id"], name: "index_blazer_audits_on_user_id"
  end

  create_table "blazer_checks", force: :cascade do |t|
    t.bigint "creator_id"
    t.bigint "query_id"
    t.string "state"
    t.string "schedule"
    t.text "emails"
    t.text "slack_channels"
    t.string "check_type"
    t.text "message"
    t.datetime "last_run_at"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["creator_id"], name: "index_blazer_checks_on_creator_id"
    t.index ["query_id"], name: "index_blazer_checks_on_query_id"
  end

  create_table "blazer_dashboard_queries", force: :cascade do |t|
    t.bigint "dashboard_id"
    t.bigint "query_id"
    t.integer "position"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["dashboard_id"], name: "index_blazer_dashboard_queries_on_dashboard_id"
    t.index ["query_id"], name: "index_blazer_dashboard_queries_on_query_id"
  end

  create_table "blazer_dashboards", force: :cascade do |t|
    t.bigint "creator_id"
    t.string "name"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["creator_id"], name: "index_blazer_dashboards_on_creator_id"
  end

  create_table "blazer_queries", force: :cascade do |t|
    t.bigint "creator_id"
    t.string "name"
    t.text "description"
    t.text "statement"
    t.string "data_source"
    t.string "status"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["creator_id"], name: "index_blazer_queries_on_creator_id"
  end

  create_table "cancellation_policies", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "hotel_id"
    t.string "name", null: false
    t.string "code", null: false
    t.text "description"
    t.string "cancellation_type", null: false
    t.integer "free_cancellation_hours"
    t.decimal "penalty_percentage", precision: 5, scale: 2
    t.integer "penalty_nights"
    t.decimal "no_show_penalty", precision: 5, scale: 2
    t.boolean "is_refundable", default: true
    t.boolean "is_default", default: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "hotel_id", "is_default"], name: "index_cancellation_policies_on_account_hotel_default"
    t.index ["account_id"], name: "index_cancellation_policies_on_account_id"
    t.index ["hotel_id"], name: "index_cancellation_policies_on_hotel_id"
  end

  create_table "chat_messages", force: :cascade do |t|
    t.bigint "chat_session_id", null: false
    t.string "role"
    t.text "content"
    t.json "metadata"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["chat_session_id"], name: "index_chat_messages_on_chat_session_id"
  end

  create_table "chat_sessions", force: :cascade do |t|
    t.string "session_id"
    t.bigint "hotel_id"
    t.boolean "archived"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["hotel_id"], name: "index_chat_sessions_on_hotel_id"
  end

  create_table "guests", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.string "first_name", null: false
    t.string "last_name", null: false
    t.string "email", null: false
    t.string "phone"
    t.date "date_of_birth"
    t.string "nationality"
    t.string "passport_number"
    t.string "id_number"
    t.string "address"
    t.string "city"
    t.string "state"
    t.string "country"
    t.string "postal_code"
    t.boolean "vip_status", default: false
    t.text "notes"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "email"], name: "index_guests_on_account_id_and_email"
    t.index ["account_id", "last_name"], name: "index_guests_on_account_id_and_last_name"
    t.index ["account_id"], name: "index_guests_on_account_id"
  end

  create_table "hotel_ai_settings", force: :cascade do |t|
    t.bigint "hotel_id", null: false
    t.bigint "account_id", null: false
    t.string "deployment_key", null: false
    t.boolean "enabled", default: false
    t.json "settings", default: {}
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id"], name: "index_hotel_ai_settings_on_account_id"
    t.index ["hotel_id", "deployment_key"], name: "index_hotel_ai_settings_on_hotel_id_and_deployment_key", unique: true
    t.index ["hotel_id"], name: "index_hotel_ai_settings_on_hotel_id"
  end

  create_table "hotel_amenities", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "hotel_id", null: false
    t.bigint "amenity_id", null: false
    t.boolean "is_available", default: true, null: false
    t.boolean "is_free", default: true, null: false
    t.decimal "additional_cost", precision: 10, scale: 2
    t.string "location"
    t.string "operating_hours"
    t.text "notes"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "hotel_id", "is_available"], name: "idx_on_account_id_hotel_id_is_available_6f4a4fce28"
    t.index ["account_id"], name: "index_hotel_amenities_on_account_id"
    t.index ["amenity_id"], name: "index_hotel_amenities_on_amenity_id"
    t.index ["hotel_id", "amenity_id"], name: "index_hotel_amenities_on_hotel_id_and_amenity_id", unique: true
    t.index ["hotel_id", "is_available", "is_free"], name: "index_hotel_amenities_on_hotel_id_and_is_available_and_is_free"
    t.index ["hotel_id"], name: "index_hotel_amenities_on_hotel_id"
  end

  create_table "hotel_policies", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "hotel_id", null: false
    t.string "policy_type", null: false
    t.string "title", null: false
    t.text "description", null: false
    t.integer "sort_order", default: 0
    t.boolean "is_active", default: true
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "hotel_id", "policy_type", "is_active"], name: "index_hotel_policies_on_account_hotel_type_active"
    t.index ["account_id"], name: "index_hotel_policies_on_account_id"
    t.index ["hotel_id"], name: "index_hotel_policies_on_hotel_id"
  end

  create_table "hotels", force: :cascade do |t|
    t.string "name"
    t.string "subdomain"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.bigint "account_id", null: false
    t.string "address", default: "", null: false
    t.string "city", default: "", null: false
    t.string "state", default: "", null: false
    t.string "country", default: "", null: false
    t.string "postal_code", default: "", null: false
    t.string "phone", default: "", null: false
    t.string "email", default: "", null: false
    t.string "website", default: ""
    t.text "description", default: ""
    t.time "check_in_time", default: "2000-01-01 15:00:00"
    t.time "check_out_time", default: "2000-01-01 11:00:00"
    t.string "timezone", default: "UTC", null: false
    t.decimal "star_rating", precision: 2, scale: 1
    t.integer "total_rooms", default: 0
    t.index ["account_id", "subdomain"], name: "index_hotels_on_account_id_and_subdomain", unique: true
    t.index ["account_id"], name: "index_hotels_on_account_id"
  end

  create_table "housekeeping_tasks", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "hotel_id", null: false
    t.bigint "room_id", null: false
    t.bigint "assigned_to_id"
    t.string "task_type", null: false
    t.string "priority", default: "normal"
    t.string "status", default: "pending"
    t.date "scheduled_date", null: false
    t.time "scheduled_time"
    t.datetime "started_at"
    t.datetime "completed_at"
    t.integer "inspection_score"
    t.text "notes"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "hotel_id", "scheduled_date", "status"], name: "index_housekeeping_tasks_on_account_hotel_date_status"
    t.index ["account_id", "room_id", "status"], name: "index_housekeeping_tasks_on_account_room_status"
    t.index ["assigned_to_id"], name: "index_housekeeping_tasks_on_assigned_to_id"
    t.index ["hotel_id"], name: "index_housekeeping_tasks_on_hotel_id"
    t.index ["room_id"], name: "index_housekeeping_tasks_on_room_id"
  end

  create_table "maintenance_requests", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "hotel_id", null: false
    t.bigint "room_id"
    t.bigint "reported_by_id", null: false
    t.bigint "assigned_to_id"
    t.string "category", null: false
    t.text "issue_description", null: false
    t.string "priority", default: "normal"
    t.string "status", default: "open"
    t.decimal "cost_estimate", precision: 10, scale: 2
    t.decimal "actual_cost", precision: 10, scale: 2
    t.date "scheduled_date"
    t.datetime "completed_at"
    t.text "resolution_notes"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "hotel_id", "status"], name: "index_maintenance_requests_on_account_hotel_status"
    t.index ["account_id", "room_id", "status"], name: "index_maintenance_requests_on_account_room_status"
    t.index ["assigned_to_id"], name: "index_maintenance_requests_on_assigned_to_id"
    t.index ["hotel_id"], name: "index_maintenance_requests_on_hotel_id"
    t.index ["reported_by_id"], name: "index_maintenance_requests_on_reported_by_id"
    t.index ["room_id"], name: "index_maintenance_requests_on_room_id"
  end

  create_table "reservations", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "hotel_id", null: false
    t.bigint "guest_id", null: false
    t.bigint "room_id"
    t.bigint "room_type_id", null: false
    t.string "confirmation_number", null: false
    t.date "check_in_date", null: false
    t.date "check_out_date", null: false
    t.datetime "actual_check_in"
    t.datetime "actual_check_out"
    t.string "status", default: "pending"
    t.integer "adults", default: 1
    t.integer "children", default: 0
    t.integer "infants", default: 0
    t.decimal "total_amount", precision: 10, scale: 2, null: false
    t.decimal "paid_amount", precision: 10, scale: 2, default: "0.0"
    t.string "currency", default: "USD"
    t.string "payment_status", default: "pending"
    t.text "special_requests"
    t.text "internal_notes"
    t.string "source"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "confirmation_number"], name: "index_reservations_on_account_id_and_confirmation_number", unique: true
    t.index ["account_id", "hotel_id", "check_in_date"], name: "idx_on_account_id_hotel_id_check_in_date_7087d23249"
    t.index ["account_id", "hotel_id", "status"], name: "index_reservations_on_account_id_and_hotel_id_and_status"
    t.index ["account_id"], name: "index_reservations_on_account_id"
    t.index ["guest_id"], name: "index_reservations_on_guest_id"
    t.index ["hotel_id"], name: "index_reservations_on_hotel_id"
    t.index ["room_id"], name: "index_reservations_on_room_id"
    t.index ["room_type_id"], name: "index_reservations_on_room_type_id"
  end

  create_table "roles", force: :cascade do |t|
    t.string "name"
    t.string "resource_type"
    t.bigint "resource_id"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["name", "resource_type", "resource_id"], name: "index_roles_on_name_and_resource_type_and_resource_id"
    t.index ["resource_type", "resource_id"], name: "index_roles_on_resource"
  end

  create_table "room_type_amenities", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "room_type_id", null: false
    t.bigint "amenity_id", null: false
    t.integer "quantity", default: 1, null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "room_type_id"], name: "index_room_type_amenities_on_account_id_and_room_type_id"
    t.index ["account_id"], name: "index_room_type_amenities_on_account_id"
    t.index ["amenity_id"], name: "index_room_type_amenities_on_amenity_id"
    t.index ["room_type_id", "amenity_id"], name: "index_room_type_amenities_on_room_type_id_and_amenity_id", unique: true
    t.index ["room_type_id"], name: "index_room_type_amenities_on_room_type_id"
  end

  create_table "room_types", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "hotel_id", null: false
    t.string "name", null: false
    t.string "code", null: false
    t.text "description"
    t.decimal "base_price", precision: 10, scale: 2, null: false
    t.integer "max_occupancy", null: false
    t.integer "standard_occupancy", null: false
    t.decimal "extra_person_charge", precision: 10, scale: 2, default: "0.0"
    t.decimal "size_sqm"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id"], name: "index_room_types_on_account_id"
    t.index ["hotel_id", "code"], name: "index_room_types_on_hotel_id_and_code", unique: true
    t.index ["hotel_id"], name: "index_room_types_on_hotel_id"
  end

  create_table "rooms", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "hotel_id", null: false
    t.bigint "room_type_id", null: false
    t.string "room_number", null: false
    t.string "floor"
    t.string "status", default: "available"
    t.boolean "is_smoking", default: false
    t.text "notes"
    t.datetime "last_cleaned_at"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "hotel_id", "status"], name: "index_rooms_on_account_id_and_hotel_id_and_status"
    t.index ["account_id"], name: "index_rooms_on_account_id"
    t.index ["hotel_id", "room_number"], name: "index_rooms_on_hotel_id_and_room_number", unique: true
    t.index ["hotel_id"], name: "index_rooms_on_hotel_id"
    t.index ["room_type_id"], name: "index_rooms_on_room_type_id"
  end

  create_table "service_bookings", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "reservation_id"
    t.bigint "service_id", null: false
    t.bigint "guest_id", null: false
    t.date "booking_date", null: false
    t.time "booking_time"
    t.integer "duration_minutes"
    t.integer "quantity", default: 1
    t.decimal "unit_price", precision: 10, scale: 2, null: false
    t.decimal "total_price", precision: 10, scale: 2, null: false
    t.string "status", default: "pending"
    t.text "notes"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "booking_date", "status"], name: "idx_on_account_id_booking_date_status_e0ad81b3d5"
    t.index ["account_id", "service_id", "booking_date"], name: "idx_on_account_id_service_id_booking_date_e8b5f59c22"
    t.index ["account_id"], name: "index_service_bookings_on_account_id"
    t.index ["guest_id"], name: "index_service_bookings_on_guest_id"
    t.index ["reservation_id"], name: "index_service_bookings_on_reservation_id"
    t.index ["service_id"], name: "index_service_bookings_on_service_id"
  end

  create_table "service_schedules", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "service_id", null: false
    t.integer "day_of_week", null: false
    t.time "start_time", null: false
    t.time "end_time", null: false
    t.integer "max_bookings_per_slot"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "service_id", "day_of_week"], name: "idx_on_account_id_service_id_day_of_week_fa26cada02"
    t.index ["account_id"], name: "index_service_schedules_on_account_id"
    t.index ["service_id"], name: "index_service_schedules_on_service_id"
  end

  create_table "services", force: :cascade do |t|
    t.bigint "account_id", null: false
    t.bigint "hotel_id", null: false
    t.string "name", null: false
    t.string "category", null: false
    t.text "description"
    t.decimal "price", precision: 10, scale: 2, null: false
    t.string "pricing_unit", default: "fixed"
    t.integer "duration_minutes"
    t.integer "capacity"
    t.boolean "advance_booking_required", default: false
    t.boolean "is_active", default: true
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["account_id", "hotel_id", "is_active"], name: "index_services_on_account_id_and_hotel_id_and_is_active"
    t.index ["account_id"], name: "index_services_on_account_id"
    t.index ["hotel_id"], name: "index_services_on_hotel_id"
  end

  create_table "users", force: :cascade do |t|
    t.string "email", default: "", null: false
    t.string "encrypted_password", default: "", null: false
    t.string "reset_password_token"
    t.datetime "reset_password_sent_at"
    t.datetime "remember_created_at"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.string "first_name"
    t.string "last_name"
    t.string "invitation_token"
    t.datetime "invitation_created_at"
    t.datetime "invitation_sent_at"
    t.datetime "invitation_accepted_at"
    t.integer "invitation_limit"
    t.string "invited_by_type"
    t.bigint "invited_by_id"
    t.integer "invitations_count", default: 0
    t.integer "sign_in_count", default: 0, null: false
    t.datetime "current_sign_in_at"
    t.datetime "last_sign_in_at"
    t.string "current_sign_in_ip"
    t.string "last_sign_in_ip"
    t.boolean "is_ai_agent", default: false, null: false
    t.boolean "is_platform_admin", default: false, null: false
    t.boolean "force_password_change", default: false, null: false
    t.index ["email"], name: "index_users_on_email", unique: true
    t.index ["invitation_token"], name: "index_users_on_invitation_token", unique: true
    t.index ["invited_by_id"], name: "index_users_on_invited_by_id"
    t.index ["invited_by_type", "invited_by_id"], name: "index_users_on_invited_by"
    t.index ["is_ai_agent"], name: "index_users_on_is_ai_agent"
    t.index ["last_sign_in_at"], name: "index_users_on_last_sign_in_at"
    t.index ["reset_password_token"], name: "index_users_on_reset_password_token", unique: true
  end

  add_foreign_key "account_users", "accounts"
  add_foreign_key "account_users", "users"
  add_foreign_key "amenities", "accounts"
  add_foreign_key "cancellation_policies", "accounts"
  add_foreign_key "cancellation_policies", "hotels"
  add_foreign_key "chat_messages", "chat_sessions"
  add_foreign_key "chat_sessions", "hotels"
  add_foreign_key "guests", "accounts"
  add_foreign_key "hotel_ai_settings", "accounts"
  add_foreign_key "hotel_ai_settings", "hotels"
  add_foreign_key "hotel_amenities", "accounts"
  add_foreign_key "hotel_amenities", "amenities"
  add_foreign_key "hotel_amenities", "hotels"
  add_foreign_key "hotel_policies", "accounts"
  add_foreign_key "hotel_policies", "hotels"
  add_foreign_key "hotels", "accounts"
  add_foreign_key "housekeeping_tasks", "accounts"
  add_foreign_key "housekeeping_tasks", "hotels"
  add_foreign_key "housekeeping_tasks", "rooms"
  add_foreign_key "housekeeping_tasks", "users", column: "assigned_to_id"
  add_foreign_key "maintenance_requests", "accounts"
  add_foreign_key "maintenance_requests", "hotels"
  add_foreign_key "maintenance_requests", "rooms"
  add_foreign_key "maintenance_requests", "users", column: "assigned_to_id"
  add_foreign_key "maintenance_requests", "users", column: "reported_by_id"
  add_foreign_key "reservations", "accounts"
  add_foreign_key "reservations", "guests"
  add_foreign_key "reservations", "hotels"
  add_foreign_key "reservations", "room_types"
  add_foreign_key "reservations", "rooms"
  add_foreign_key "room_type_amenities", "accounts"
  add_foreign_key "room_type_amenities", "amenities"
  add_foreign_key "room_type_amenities", "room_types"
  add_foreign_key "room_types", "accounts"
  add_foreign_key "room_types", "hotels"
  add_foreign_key "rooms", "accounts"
  add_foreign_key "rooms", "hotels"
  add_foreign_key "rooms", "room_types"
  add_foreign_key "service_bookings", "accounts"
  add_foreign_key "service_bookings", "guests"
  add_foreign_key "service_bookings", "reservations"
  add_foreign_key "service_bookings", "services"
  add_foreign_key "service_schedules", "accounts"
  add_foreign_key "service_schedules", "services"
  add_foreign_key "services", "accounts"
  add_foreign_key "services", "hotels"
end
