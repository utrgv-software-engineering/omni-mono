class AddForcePasswordChangeToUsers < ActiveRecord::Migration[8.0]
  def change
    add_column :users, :force_password_change, :boolean, default: false, null: false
  end
end
