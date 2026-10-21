class CreateChatSessions < ActiveRecord::Migration[8.0]
  def change
    create_table :chat_sessions do |t|
      t.string :session_id
      t.string :guest_email
      t.string :guest_name
      t.references :hotel, null: false, foreign_key: true
      t.boolean :archived

      t.timestamps
    end
  end
end
