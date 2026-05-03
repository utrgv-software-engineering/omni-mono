# == Schema Information
#
# Table name: room_types
#
#  id                  :bigint           not null, primary key
#  base_price          :decimal(10, 2)   not null
#  code                :string           not null
#  description         :text
#  extra_person_charge :decimal(10, 2)   default(0.0)
#  max_occupancy       :integer          not null
#  name                :string           not null
#  size_sqm            :decimal(, )
#  standard_occupancy  :integer          not null
#  created_at          :datetime         not null
#  updated_at          :datetime         not null
#  account_id          :bigint           not null
#  hotel_id            :bigint           not null
#
# Indexes
#
#  index_room_types_on_account_id         (account_id)
#  index_room_types_on_hotel_id           (hotel_id)
#  index_room_types_on_hotel_id_and_code  (hotel_id,code) UNIQUE
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
class RoomType < ApplicationRecord
  acts_as_tenant(:account)
  belongs_to :account
  belongs_to :hotel
  has_many :rooms, dependent: :restrict_with_error
  has_many :reservations
  has_many :room_type_amenities, dependent: :destroy
  has_many :amenities, through: :room_type_amenities
  
  validates :name, presence: true
  validates :code, presence: true, uniqueness: { scope: :hotel_id }
  validates :base_price, presence: true, numericality: { greater_than: 0 }
  validates :max_occupancy, presence: true, numericality: { greater_than: 0 }
  validates :standard_occupancy, presence: true, numericality: { greater_than: 0 }
  validates :extra_person_charge, numericality: { greater_than_or_equal_to: 0 }
  validates :size_sqm, numericality: { greater_than: 0 }, allow_nil: true
  
  validate :standard_occupancy_within_max
  
  scope :available_for_dates, ->(check_in, check_out) {
    joins(:rooms).where(rooms: { status: "available" })
                 .where.not(rooms: { id: Reservation.where("check_in_date < ? AND check_out_date > ?", check_out, check_in)
                                                    .where.not(status: ["cancelled", "no_show"])
                                                    .select(:room_id) })
                 .distinct
  }
  
  def available_rooms_count(check_in_date, check_out_date)
    rooms.where(status: "available")
         .where.not(id: Reservation.where(room_type: self)
                                   .where("check_in_date < ? AND check_out_date > ?", check_out_date, check_in_date)
                                   .where.not(status: ["cancelled", "no_show"])
                                   .select(:room_id))
         .count
  end
  
  def calculate_price(nights:, guests:)
    extra_guests = [guests - standard_occupancy, 0].max
    base_total = base_price * nights
    extra_charge = extra_person_charge * extra_guests * nights
    base_total + extra_charge
  end
  
  # Amenity-related methods
  def amenities_list
    room_type_amenities.includes(:amenity).map do |rta|
      {
        name: rta.amenity.name,
        category: rta.amenity.category,
        quantity: rta.quantity,
        icon: rta.amenity.icon_class,
        display_text: rta.display_text
      }
    end
  end
  
  def amenities_by_category(category)
    amenities.where(category: category)
  end
  
  def has_amenity?(amenity_name)
    amenities.exists?(name: amenity_name)
  end
  
  def amenity_quantity(amenity_name)
    room_type_amenities.joins(:amenity)
                       .where(amenities: { name: amenity_name })
                       .pluck(:quantity)
                       .first || 0
  end
  
  private
  
  def standard_occupancy_within_max
    return unless standard_occupancy && max_occupancy
    
    if standard_occupancy > max_occupancy
      errors.add(:standard_occupancy, "cannot be greater than maximum occupancy")
    end
  end
end
