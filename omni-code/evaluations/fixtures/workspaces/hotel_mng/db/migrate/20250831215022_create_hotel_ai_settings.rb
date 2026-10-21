class CreateHotelAiSettings < ActiveRecord::Migration[8.0]
  def change
    create_table :hotel_ai_settings do |t|
      t.references :hotel, null: false, foreign_key: true
      t.references :account, null: false, foreign_key: true
      t.string :deployment_key, null: false
      t.boolean :enabled, default: false
      t.jsonb :settings, default: {}
      t.timestamps
    end
    
    add_index :hotel_ai_settings, [:hotel_id, :deployment_key], unique: true
  end
end