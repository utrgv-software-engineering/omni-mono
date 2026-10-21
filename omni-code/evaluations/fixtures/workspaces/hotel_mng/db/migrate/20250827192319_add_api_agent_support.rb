class AddApiAgentSupport < ActiveRecord::Migration[8.0]
  def change
    # User model: track if user is an AI agent
    add_column :users, :is_ai_agent, :boolean, default: false, null: false
    add_index :users, :is_ai_agent
    
    # AccountUser model: API keys scoped to account relationship
    add_column :account_users, :api_key, :string
    add_column :account_users, :api_key_last_used_at, :datetime
    add_index :account_users, :api_key, unique: true
  end
end
