class RemoveGuestFieldsFromChatSessions < ActiveRecord::Migration[8.0]
  def change
    remove_column :chat_sessions, :guest_name, :string
    remove_column :chat_sessions, :guest_email, :string
  end
end