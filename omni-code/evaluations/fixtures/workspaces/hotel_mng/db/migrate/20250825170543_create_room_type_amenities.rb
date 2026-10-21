class CreateRoomTypeAmenities < ActiveRecord::Migration[8.0]
  def change
    create_table :room_type_amenities do |t|
      t.references :account, null: false, foreign_key: true
      t.references :room_type, null: false, foreign_key: true
      t.references :amenity, null: false, foreign_key: true
      t.integer :quantity, default: 1, null: false

      t.timestamps
    end

    # Add unique constraint to prevent duplicate amenities per room type
    add_index :room_type_amenities, [:room_type_id, :amenity_id], unique: true
    # Add index for account scoping
    add_index :room_type_amenities, [:account_id, :room_type_id]
  end
end
