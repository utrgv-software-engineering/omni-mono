class RolifyCreateRoles < ActiveRecord::Migration[8.0]
  def change
    create_table(:roles) do |t|
      t.string :name
      t.references :resource, :polymorphic => true

      t.timestamps
    end

    create_table(:account_users_roles, :id => false) do |t|
      t.references :account_user
      t.references :role
    end
    
    add_index(:roles, [ :name, :resource_type, :resource_id ])
    add_index(:account_users_roles, [ :account_user_id, :role_id ])
  end
end
