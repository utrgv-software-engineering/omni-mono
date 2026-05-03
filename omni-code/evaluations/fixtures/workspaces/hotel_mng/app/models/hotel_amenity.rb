# frozen_string_literal: true

# == Schema Information
#
# Table name: hotel_amenities
#
#  id              :bigint           not null, primary key
#  additional_cost :decimal(10, 2)
#  is_available    :boolean          default(TRUE), not null
#  is_free         :boolean          default(TRUE), not null
#  location        :string
#  notes           :text
#  operating_hours :string
#  created_at      :datetime         not null
#  updated_at      :datetime         not null
#  account_id      :bigint           not null
#  amenity_id      :bigint           not null
#  hotel_id        :bigint           not null
#
# Indexes
#
#  idx_on_account_id_hotel_id_is_available_6f4a4fce28              (account_id,hotel_id,is_available)
#  index_hotel_amenities_on_account_id                             (account_id)
#  index_hotel_amenities_on_amenity_id                             (amenity_id)
#  index_hotel_amenities_on_hotel_id                               (hotel_id)
#  index_hotel_amenities_on_hotel_id_and_amenity_id                (hotel_id,amenity_id) UNIQUE
#  index_hotel_amenities_on_hotel_id_and_is_available_and_is_free  (hotel_id,is_available,is_free)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (amenity_id => amenities.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
class HotelAmenity < ApplicationRecord
  acts_as_tenant(:account)
  
  # Associations
  belongs_to :account
  belongs_to :hotel
  belongs_to :amenity
  
  # Validations
  validates :amenity_id, uniqueness: { scope: :hotel_id }
  validates :additional_cost, numericality: { greater_than_or_equal_to: 0 }, allow_nil: true
  
  # Scopes
  scope :free, -> { where(is_free: true) }
  scope :paid, -> { where(is_free: false) }
  scope :available, -> { where(is_available: true) }
  scope :unavailable, -> { where(is_available: false) }
  
  # Include amenity details for easier access
  scope :with_amenity_details, -> { includes(:amenity) }
  
  # Default scope to only show available amenities
  default_scope { available }
  
  # Callbacks
  before_validation :set_defaults
  
  # Check if the amenity has a cost
  def has_cost?
    !is_free && additional_cost.to_f > 0
  end
  
  # Get the display price
  def display_price
    return "Free" if is_free
    return "Inquire" if additional_cost.nil?
    "$#{additional_cost}"
  end
  
  # Get operating hours in a formatted way
  def formatted_operating_hours
    return "24/7" if operating_hours.blank?
    operating_hours
  end
  
  # Check if currently open (basic implementation - can be enhanced)
  def currently_open?
    return true if operating_hours.blank? || operating_hours == "24/7"
    # This would need more complex logic based on actual hours format
    true
  end
  
  private
  
  def set_defaults
    self.is_free = true if is_free.nil?
    self.is_available = true if is_available.nil?
  end
end
