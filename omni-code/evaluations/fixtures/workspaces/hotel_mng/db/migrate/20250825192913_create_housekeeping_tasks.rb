class CreateHousekeepingTasks < ActiveRecord::Migration[8.0]
  def change
    create_table :housekeeping_tasks do |t|
      t.bigint :account_id, null: false
      t.bigint :hotel_id, null: false
      t.bigint :room_id, null: false
      t.bigint :assigned_to_id
      t.string :task_type, null: false
      t.string :priority, default: 'normal'
      t.string :status, default: 'pending'
      t.date :scheduled_date, null: false
      t.time :scheduled_time
      t.datetime :started_at
      t.datetime :completed_at
      t.integer :inspection_score
      t.text :notes

      t.timestamps
    end

    add_index :housekeeping_tasks, [:account_id, :hotel_id, :scheduled_date, :status], 
              name: 'index_housekeeping_tasks_on_account_hotel_date_status'
    add_index :housekeeping_tasks, [:account_id, :room_id, :status],
              name: 'index_housekeeping_tasks_on_account_room_status'
    add_index :housekeeping_tasks, :hotel_id
    add_index :housekeeping_tasks, :room_id
    add_index :housekeeping_tasks, :assigned_to_id
    
    add_foreign_key :housekeeping_tasks, :accounts
    add_foreign_key :housekeeping_tasks, :hotels
    add_foreign_key :housekeeping_tasks, :rooms
    add_foreign_key :housekeeping_tasks, :users, column: :assigned_to_id
  end
end
