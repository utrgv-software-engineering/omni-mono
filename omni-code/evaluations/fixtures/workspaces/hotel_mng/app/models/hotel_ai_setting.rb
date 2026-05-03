# == Schema Information
#
# Table name: hotel_ai_settings
#
#  id             :bigint           not null, primary key
#  deployment_key :string           not null
#  enabled        :boolean          default(FALSE)
#  settings       :jsonb
#  created_at     :datetime         not null
#  updated_at     :datetime         not null
#  account_id     :bigint           not null
#  hotel_id       :bigint           not null
#
# Indexes
#
#  index_hotel_ai_settings_on_account_id                   (account_id)
#  index_hotel_ai_settings_on_hotel_id                     (hotel_id)
#  index_hotel_ai_settings_on_hotel_id_and_deployment_key  (hotel_id,deployment_key) UNIQUE
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
class HotelAiSetting < ApplicationRecord
  belongs_to :hotel
  belongs_to :account
  acts_as_tenant(:account)
  
  # Fields:
  # - deployment_key: string (e.g., 'web_chat')
  # - enabled: boolean
  # - settings: jsonb (for future customization)
  
  validates :deployment_key, presence: true, uniqueness: { scope: :hotel_id }
  
  # Only hotel-scoped deployments can be stored
  validate :deployment_must_be_hotel_scoped
  
  before_validation :set_account_from_hotel
  
  scope :enabled, -> { where(enabled: true) }
  scope :for_deployment, ->(key) { where(deployment_key: key) }
  
  private
  
  def deployment_must_be_hotel_scoped
    return if deployment_key.blank?
    
    unless AiDeployments.hotel_deployments.key?(deployment_key.to_sym)
      errors.add(:deployment_key, "must be a valid hotel deployment")
    end
  end
  
  def set_account_from_hotel
    self.account = hotel.account if hotel.present?
  end
end
