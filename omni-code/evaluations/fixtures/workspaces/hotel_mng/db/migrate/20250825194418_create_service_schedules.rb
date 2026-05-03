class CreateServiceSchedules < ActiveRecord::Migration[8.0]
  def change
    create_table :service_schedules do |t|
      t.references :account, null: false, foreign_key: true
      t.references :service, null: false, foreign_key: true
      t.integer :day_of_week, null: false
      t.time :start_time, null: false
      t.time :end_time, null: false
      t.integer :max_bookings_per_slot

      t.timestamps
    end

    add_index :service_schedules, [:account_id, :service_id, :day_of_week]
  end
end
