class CreateRooms < ActiveRecord::Migration[8.0]
  def change
    create_table :rooms do |t|
      t.references :account, null: false, foreign_key: true
      t.references :hotel, null: false, foreign_key: true
      t.references :room_type, null: false, foreign_key: true
      t.string :room_number, null: false
      t.string :floor
      t.string :status, default: "available"
      t.boolean :is_smoking, default: false
      t.text :notes
      t.datetime :last_cleaned_at

      t.timestamps
    end
    
    add_index :rooms, [:account_id, :hotel_id, :status]
    add_index :rooms, [:hotel_id, :room_number], unique: true
  end
end
