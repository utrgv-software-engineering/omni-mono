class MakeHotelOptionalForChatSessions < ActiveRecord::Migration[8.0]
  def change
    change_column_null :chat_sessions, :hotel_id, true
  end
end