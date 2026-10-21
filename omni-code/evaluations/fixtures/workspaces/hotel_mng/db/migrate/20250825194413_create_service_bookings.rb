class CreateServiceBookings < ActiveRecord::Migration[8.0]
  def change
    create_table :service_bookings do |t|
      t.references :account, null: false, foreign_key: true
      t.references :reservation, foreign_key: true
      t.references :service, null: false, foreign_key: true
      t.references :guest, null: false, foreign_key: true
      t.date :booking_date, null: false
      t.time :booking_time
      t.integer :duration_minutes
      t.integer :quantity, default: 1
      t.decimal :unit_price, precision: 10, scale: 2, null: false
      t.decimal :total_price, precision: 10, scale: 2, null: false
      t.string :status, default: 'pending'
      t.text :notes

      t.timestamps
    end

    add_index :service_bookings, [:account_id, :booking_date, :status]
    add_index :service_bookings, [:account_id, :service_id, :booking_date]
  end
end
