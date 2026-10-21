class CreateGuests < ActiveRecord::Migration[8.0]
  def change
    create_table :guests do |t|
      t.references :account, null: false, foreign_key: true
      t.string :first_name, null: false
      t.string :last_name, null: false
      t.string :email, null: false
      t.string :phone
      t.date :date_of_birth
      t.string :nationality
      t.string :passport_number
      t.string :id_number
      t.string :address
      t.string :city
      t.string :state
      t.string :country
      t.string :postal_code
      t.boolean :vip_status, default: false
      t.text :notes

      t.timestamps
    end
    
    add_index :guests, [:account_id, :email]
    add_index :guests, [:account_id, :last_name]
  end
end
