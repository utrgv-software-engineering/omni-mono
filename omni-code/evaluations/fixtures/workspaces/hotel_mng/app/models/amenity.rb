# frozen_string_literal: true

# == Schema Information
#
# Table name: amenities
#
#  id           :bigint           not null, primary key
#  category     :string           not null
#  description  :text
#  icon         :string
#  is_countable :boolean          default(FALSE), not null
#  name         :string           not null
#  created_at   :datetime         not null
#  updated_at   :datetime         not null
#  account_id   :bigint           not null
#
# Indexes
#
#  index_amenities_on_account_id               (account_id)
#  index_amenities_on_account_id_and_category  (account_id,category)
#  index_amenities_on_account_id_and_name      (account_id,name) UNIQUE
#  index_amenities_on_category                 (category)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#
class Amenity < ApplicationRecord
  acts_as_tenant(:account)
  
  # Define available categories for amenities
  CATEGORIES = %w[
    room 
    hotel 
    bathroom 
    kitchen 
    entertainment 
    accessibility
    business
    family
    outdoor
    wellness
  ].freeze
  
  # Icon suggestions for common amenities (can be Font Awesome classes or custom)
  ICON_MAPPINGS = {
    'WiFi' => 'wifi',
    'Pool' => 'person-swimming',
    'Gym' => 'dumbbell',
    'Parking' => 'square-parking',
    'Restaurant' => 'utensils',
    'Bar' => 'martini-glass',
    'Air Conditioning' => 'snowflake',
    'TV' => 'tv',
    'Safe' => 'vault',
    'Coffee Maker' => 'mug-hot'
  }.freeze
  
  # Associations
  belongs_to :account
  has_many :hotel_amenities, dependent: :destroy
  has_many :hotels, through: :hotel_amenities
  has_many :room_type_amenities, dependent: :destroy
  has_many :room_types, through: :room_type_amenities
  
  # Validations
  validates :name, presence: true, uniqueness: { scope: :account_id, case_sensitive: false }
  validates :category, presence: true, inclusion: { in: CATEGORIES }
  
  # Scopes for filtering amenities by category
  scope :hotel_level, -> { where(category: %w[hotel outdoor wellness business]) }
  scope :room_level, -> { where(category: %w[room bathroom kitchen entertainment]) }
  scope :accessibility, -> { where(category: 'accessibility') }
  scope :family_friendly, -> { where(category: 'family') }
  scope :countable, -> { where(is_countable: true) }
  
  # Default scope to order by category and name
  default_scope { order(:category, :name) }
  
  # Check if amenity is typically a hotel-level amenity
  def hotel_amenity?
    %w[hotel outdoor wellness business].include?(category)
  end
  
  # Check if amenity is typically a room-level amenity
  def room_amenity?
    %w[room bathroom kitchen entertainment].include?(category)
  end
  
  # Get icon class for the amenity
  def icon_class
    return icon if icon.present?
    ICON_MAPPINGS[name] || 'circle'
  end
  
  # Display name with category
  def display_name
    "#{name} (#{category.humanize})"
  end
end
