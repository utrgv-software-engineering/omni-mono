class CreateHotelPolicies < ActiveRecord::Migration[8.0]
  def change
    create_table :hotel_policies do |t|
      t.references :account, null: false, foreign_key: true
      t.references :hotel, null: false, foreign_key: true
      t.string :policy_type, null: false
      t.string :title, null: false
      t.text :description, null: false
      t.integer :sort_order, default: 0
      t.boolean :is_active, default: true

      t.timestamps
    end

    add_index :hotel_policies, [:account_id, :hotel_id, :policy_type, :is_active], name: 'index_hotel_policies_on_account_hotel_type_active'
  end
end
