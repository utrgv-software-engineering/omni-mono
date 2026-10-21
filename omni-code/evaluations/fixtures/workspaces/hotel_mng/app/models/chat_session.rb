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
class ChatSession < ApplicationRecord
  belongs_to :hotel, optional: true
  has_many :chat_messages, dependent: :destroy
  
  scope :active, -> { where(archived: [false, nil]) }
  scope :archived, -> { where(archived: true) }
  scope :platform, -> { where(hotel_id: nil) }
  scope :hotel_sessions, -> { where.not(hotel_id: nil) }
  
  before_create :generate_session_id
  
  def platform_session?
    hotel_id.nil?
  end
  
  def hotel_session?
    hotel_id.present?
  end
  
  private
  
  def generate_session_id
    self.session_id ||= SecureRandom.uuid
  end
end
