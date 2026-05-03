class CreateAmenities < ActiveRecord::Migration[8.0]
  def change
    create_table :amenities do |t|
      t.references :account, null: false, foreign_key: true
      t.string :name, null: false
      t.string :category, null: false
      t.string :icon
      t.text :description
      t.boolean :is_countable, default: false, null: false

      t.timestamps
    end

    # Add indexes for performance
    add_index :amenities, [:account_id, :name], unique: true
    add_index :amenities, [:account_id, :category]
    add_index :amenities, :category
  end
end