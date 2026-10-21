class CreateHotelAmenities < ActiveRecord::Migration[8.0]
  def change
    create_table :hotel_amenities do |t|
      t.references :account, null: false, foreign_key: true
      t.references :hotel, null: false, foreign_key: true
      t.references :amenity, null: false, foreign_key: true
      t.boolean :is_available, default: true, null: false
      t.boolean :is_free, default: true, null: false
      t.decimal :additional_cost, precision: 10, scale: 2
      t.string :location
      t.string :operating_hours
      t.text :notes

      t.timestamps
    end

    # Add unique constraint to prevent duplicate amenities per hotel
    add_index :hotel_amenities, [:hotel_id, :amenity_id], unique: true
    # Add indexes for common queries
    add_index :hotel_amenities, [:account_id, :hotel_id, :is_available]
    add_index :hotel_amenities, [:hotel_id, :is_available, :is_free]
  end
end
