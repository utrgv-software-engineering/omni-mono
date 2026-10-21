# == Schema Information
#
# Table name: chat_sessions
#
#  id         :bigint           not null, primary key
#  archived   :boolean
#  created_at :datetime         not null
#  updated_at :datetime         not null
#  hotel_id   :bigint
#  session_id :string
#
# Indexes
#
#  index_chat_sessions_on_hotel_id  (hotel_id)
#
# Foreign Keys
#
#  fk_rails_...  (hotel_id => hotels.id)
#
FactoryBot.define do
  factory :chat_session do
    session_id { "MyString" }
    guest_email { "MyString" }
    guest_name { "MyString" }
    hotel { nil }
    archived { false }
  end
end
