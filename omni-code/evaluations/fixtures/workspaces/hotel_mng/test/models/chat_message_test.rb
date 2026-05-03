# == Schema Information
#
# Table name: chat_messages
#
#  id              :bigint           not null, primary key
#  content         :text
#  metadata        :jsonb
#  role            :string
#  created_at      :datetime         not null
#  updated_at      :datetime         not null
#  chat_session_id :bigint           not null
#
# Indexes
#
#  index_chat_messages_on_chat_session_id  (chat_session_id)
#
# Foreign Keys
#
#  fk_rails_...  (chat_session_id => chat_sessions.id)
#
require "test_helper"

class ChatMessageTest < ActiveSupport::TestCase
  # test "the truth" do
  #   assert true
  # end
end
