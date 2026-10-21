# == Schema Information
#
# Table name: hotel_policies
#
#  id          :bigint           not null, primary key
#  description :text             not null
#  is_active   :boolean          default(TRUE)
#  policy_type :string           not null
#  sort_order  :integer          default(0)
#  title       :string           not null
#  created_at  :datetime         not null
#  updated_at  :datetime         not null
#  account_id  :bigint           not null
#  hotel_id    :bigint           not null
#
# Indexes
#
#  index_hotel_policies_on_account_hotel_type_active  (account_id,hotel_id,policy_type,is_active)
#  index_hotel_policies_on_account_id                 (account_id)
#  index_hotel_policies_on_hotel_id                   (hotel_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
class HotelPolicy < ApplicationRecord
  acts_as_tenant(:account)
  
  belongs_to :account
  belongs_to :hotel
  
  POLICY_TYPES = %w[check_in check_out cancellation pet smoking children].freeze
  
  validates :policy_type, presence: true, inclusion: { in: POLICY_TYPES }
  validates :title, presence: true
  validates :description, presence: true
  validates :sort_order, numericality: { greater_than_or_equal_to: 0 }
  
  scope :active, -> { where(is_active: true) }
  scope :by_type, ->(type) { where(policy_type: type) }
  scope :ordered, -> { order(:sort_order, :created_at) }
  
  def activate!
    update!(is_active: true)
  end
  
  def deactivate!
    update!(is_active: false)
  end
end
