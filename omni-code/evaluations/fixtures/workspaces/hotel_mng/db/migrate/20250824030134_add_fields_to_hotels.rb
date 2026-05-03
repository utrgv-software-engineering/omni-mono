class AddFieldsToHotels < ActiveRecord::Migration[8.0]
  def change
    add_column :hotels, :address, :string, null: false, default: ""
    add_column :hotels, :city, :string, null: false, default: ""
    add_column :hotels, :state, :string, null: false, default: ""
    add_column :hotels, :country, :string, null: false, default: ""
    add_column :hotels, :postal_code, :string, null: false, default: ""
    add_column :hotels, :phone, :string, null: false, default: ""
    add_column :hotels, :email, :string, null: false, default: ""
    add_column :hotels, :website, :string
    add_column :hotels, :description, :text
    add_column :hotels, :check_in_time, :time, default: "15:00"
    add_column :hotels, :check_out_time, :time, default: "11:00"
    add_column :hotels, :timezone, :string, null: false, default: "UTC"
    add_column :hotels, :star_rating, :decimal, precision: 2, scale: 1
    add_column :hotels, :total_rooms, :integer
    
    # Add unique index for subdomain scoped to account
    remove_index :hotels, :subdomain
    add_index :hotels, [:account_id, :subdomain], unique: true
  end
end
