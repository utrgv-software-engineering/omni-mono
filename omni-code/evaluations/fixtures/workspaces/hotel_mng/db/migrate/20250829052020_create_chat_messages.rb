class CreateChatMessages < ActiveRecord::Migration[8.0]
  def change
    create_table :chat_messages do |t|
      t.references :chat_session, null: false, foreign_key: true
      t.string :role
      t.text :content
      t.jsonb :metadata

      t.timestamps
    end
  end
end
