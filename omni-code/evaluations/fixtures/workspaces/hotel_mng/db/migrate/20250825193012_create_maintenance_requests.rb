class CreateMaintenanceRequests < ActiveRecord::Migration[8.0]
  def change
    create_table :maintenance_requests do |t|
      t.bigint :account_id, null: false
      t.bigint :hotel_id, null: false
      t.bigint :room_id
      t.bigint :reported_by_id, null: false
      t.bigint :assigned_to_id
      t.string :category, null: false
      t.text :issue_description, null: false
      t.string :priority, default: 'normal'
      t.string :status, default: 'open'
      t.decimal :cost_estimate, precision: 10, scale: 2
      t.decimal :actual_cost, precision: 10, scale: 2
      t.date :scheduled_date
      t.datetime :completed_at
      t.text :resolution_notes

      t.timestamps
    end

    add_index :maintenance_requests, [:account_id, :hotel_id, :status],
              name: 'index_maintenance_requests_on_account_hotel_status'
    add_index :maintenance_requests, [:account_id, :room_id, :status],
              name: 'index_maintenance_requests_on_account_room_status'
    add_index :maintenance_requests, :hotel_id
    add_index :maintenance_requests, :room_id
    add_index :maintenance_requests, :reported_by_id
    add_index :maintenance_requests, :assigned_to_id
    
    add_foreign_key :maintenance_requests, :accounts
    add_foreign_key :maintenance_requests, :hotels
    add_foreign_key :maintenance_requests, :rooms
    add_foreign_key :maintenance_requests, :users, column: :reported_by_id
    add_foreign_key :maintenance_requests, :users, column: :assigned_to_id
  end
end
