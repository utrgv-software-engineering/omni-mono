class AddFieldsToAccounts < ActiveRecord::Migration[8.0]
  def change
    add_column :accounts, :address, :string
    add_column :accounts, :city, :string
    add_column :accounts, :state, :string
    add_column :accounts, :country, :string
    add_column :accounts, :postal_code, :string
    add_column :accounts, :phone, :string
    add_column :accounts, :email, :string
    add_column :accounts, :timezone, :string, default: "UTC"
    add_column :accounts, :currency, :string, default: "USD"
    add_column :accounts, :status, :string, default: "active"
    add_column :accounts, :trial_ends_at, :datetime
  end
end
