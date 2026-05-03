class AddPlatformAdminToUsers < ActiveRecord::Migration[8.0]
  def change
    add_column :users, :is_platform_admin, :boolean, default: false, null: false
  end
end
