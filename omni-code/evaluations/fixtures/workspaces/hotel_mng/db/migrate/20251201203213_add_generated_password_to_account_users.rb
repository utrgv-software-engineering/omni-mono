class AddGeneratedPasswordToAccountUsers < ActiveRecord::Migration[8.0]
  def change
    add_column :account_users, :generated_password, :string
  end
end
