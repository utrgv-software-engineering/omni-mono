class CreateServices < ActiveRecord::Migration[8.0]
  def change
    create_table :services do |t|
      t.references :account, null: false, foreign_key: true
      t.references :hotel, null: false, foreign_key: true
      t.string :name, null: false
      t.string :category, null: false
      t.text :description
      t.decimal :price, precision: 10, scale: 2, null: false
      t.string :pricing_unit, default: 'fixed'
      t.integer :duration_minutes
      t.integer :capacity
      t.boolean :advance_booking_required, default: false
      t.boolean :is_active, default: true

      t.timestamps
    end

    add_index :services, [:account_id, :hotel_id, :is_active]
  end
end