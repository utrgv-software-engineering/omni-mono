class CreateRoomTypes < ActiveRecord::Migration[8.0]
  def change
    create_table :room_types do |t|
      t.references :account, null: false, foreign_key: true
      t.references :hotel, null: false, foreign_key: true
      t.string :name, null: false
      t.string :code, null: false
      t.text :description
      t.decimal :base_price, precision: 10, scale: 2, null: false
      t.integer :max_occupancy, null: false
      t.integer :standard_occupancy, null: false
      t.decimal :extra_person_charge, precision: 10, scale: 2, default: 0
      t.decimal :size_sqm

      t.timestamps
    end
    
    add_index :room_types, [:hotel_id, :code], unique: true
  end
end
