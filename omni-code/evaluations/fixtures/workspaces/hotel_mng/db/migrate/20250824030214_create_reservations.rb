class CreateReservations < ActiveRecord::Migration[8.0]
  def change
    create_table :reservations do |t|
      t.references :account, null: false, foreign_key: true
      t.references :hotel, null: false, foreign_key: true
      t.references :guest, null: false, foreign_key: true
      t.references :room, foreign_key: true # nullable until room assignment
      t.references :room_type, null: false, foreign_key: true
      t.string :confirmation_number, null: false
      t.date :check_in_date, null: false
      t.date :check_out_date, null: false
      t.datetime :actual_check_in
      t.datetime :actual_check_out
      t.string :status, default: "pending"
      t.integer :adults, default: 1
      t.integer :children, default: 0
      t.integer :infants, default: 0
      t.decimal :total_amount, precision: 10, scale: 2, null: false
      t.decimal :paid_amount, precision: 10, scale: 2, default: 0
      t.string :currency, default: "USD"
      t.string :payment_status, default: "pending"
      t.text :special_requests
      t.text :internal_notes
      t.string :source

      t.timestamps
    end
    
    add_index :reservations, [:account_id, :hotel_id, :check_in_date]
    add_index :reservations, [:account_id, :hotel_id, :status]
    add_index :reservations, :confirmation_number, unique: true
  end
end
