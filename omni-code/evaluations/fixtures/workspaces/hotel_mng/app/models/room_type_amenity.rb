# frozen_string_literal: true

# == Schema Information
#
# Table name: room_type_amenities
#
#  id           :bigint           not null, primary key
#  quantity     :integer          default(1), not null
#  created_at   :datetime         not null
#  updated_at   :datetime         not null
#  account_id   :bigint           not null
#  amenity_id   :bigint           not null
#  room_type_id :bigint           not null
#
# Indexes
#
#  index_room_type_amenities_on_account_id                   (account_id)
#  index_room_type_amenities_on_account_id_and_room_type_id  (account_id,room_type_id)
#  index_room_type_amenities_on_amenity_id                   (amenity_id)
#  index_room_type_amenities_on_room_type_id                 (room_type_id)
#  index_room_type_amenities_on_room_type_id_and_amenity_id  (room_type_id,amenity_id) UNIQUE
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (amenity_id => amenities.id)
#  fk_rails_...  (room_type_id => room_types.id)
#
class RoomTypeAmenity < ApplicationRecord
  acts_as_tenant(:account)
  
  # Associations
  belongs_to :account
  belongs_to :room_type
  belongs_to :amenity
  
  # Validations
  validates :amenity_id, uniqueness: { scope: :room_type_id }
  validates :quantity, numericality: { greater_than: 0 }
  
  # Scopes
  scope :with_amenity_details, -> { includes(:amenity) }
  scope :countable, -> { joins(:amenity).where(amenities: { is_countable: true }) }
  scope :non_countable, -> { joins(:amenity).where(amenities: { is_countable: false }) }
  
  # Callbacks
  before_validation :set_defaults
  
  # Delegate some methods to amenity for convenience
  delegate :name, :category, :icon, :description, to: :amenity, prefix: true
  
  # Get display text for the amenity with quantity
  def display_text
    return amenity.name if quantity == 1 || !amenity.is_countable?
    "#{quantity} #{amenity.name.pluralize}"
  end
  
  # Get full details as a hash
  def to_display_hash
    {
      name: amenity_name,
      category: amenity_category,
      icon: amenity.icon_class,
      quantity: quantity,
      display_text: display_text,
      description: amenity_description
    }
  end
  
  private
  
  def set_defaults
    self.quantity = 1 if quantity.nil?
  end
end
