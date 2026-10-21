# == Schema Information
#
# Table name: hotels
#
#  id             :bigint           not null, primary key
#  address        :string           default(""), not null
#  check_in_time  :time             default(2000-01-01 15:00:00.000000000 UTC +00:00)
#  check_out_time :time             default(2000-01-01 11:00:00.000000000 UTC +00:00)
#  city           :string           default(""), not null
#  country        :string           default(""), not null
#  description    :text             default("")
#  email          :string           default(""), not null
#  name           :string
#  phone          :string           default(""), not null
#  postal_code    :string           default(""), not null
#  star_rating    :decimal(2, 1)
#  state          :string           default(""), not null
#  subdomain      :string
#  timezone       :string           default("UTC"), not null
#  total_rooms    :integer          default(0)
#  website        :string           default("")
#  created_at     :datetime         not null
#  updated_at     :datetime         not null
#  account_id     :bigint           not null
#
# Indexes
#
#  index_hotels_on_account_id                (account_id)
#  index_hotels_on_account_id_and_subdomain  (account_id,subdomain) UNIQUE
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#
class Hotel < ApplicationRecord
  acts_as_tenant(:account)
  
  # Tell Pundit to use HotelAuthorizationPolicy for authorization
  def self.policy_class
    HotelAuthorizationPolicy
  end
  belongs_to :account
  has_many :room_types, dependent: :destroy
  has_many :rooms, dependent: :destroy
  has_many :reservations, dependent: :destroy
  has_many :hotel_amenities, dependent: :destroy
  has_many :amenities, through: :hotel_amenities
  has_many :hotel_policies, dependent: :destroy
  has_many :cancellation_policies, dependent: :destroy
  has_many :housekeeping_tasks, dependent: :destroy
  has_many :maintenance_requests, dependent: :destroy
  has_many :services, dependent: :destroy
  has_many :hotel_ai_settings, dependent: :destroy
  has_many :chat_sessions, dependent: :destroy
  
  validates :name, presence: true
  validates :subdomain, presence: true, uniqueness: { scope: :account_id }
  validates :address, presence: true
  validates :city, presence: true
  validates :state, presence: true
  validates :country, presence: true
  validates :postal_code, presence: true
  validates :phone, presence: true
  validates :email, presence: true, format: { with: URI::MailTo::EMAIL_REGEXP }
  validates :timezone, presence: true, inclusion: { in: ActiveSupport::TimeZone.all.map(&:name) }
  validates :star_rating, numericality: { greater_than_or_equal_to: 1, less_than_or_equal_to: 5 }, allow_nil: true
  validates :total_rooms, numericality: { greater_than_or_equal_to: 0 }, allow_nil: true
  
  scope :with_availability, -> { joins(:rooms).where(rooms: { status: "available" }).distinct }
  
  def available_rooms(check_in_date, check_out_date)
    rooms.where(status: "available")
         .where.not(id: Reservation.where(hotel: self)
                                   .where("check_in_date < ? AND check_out_date > ?", check_out_date, check_in_date)
                                   .where.not(status: ["cancelled", "no_show"])
                                   .select(:room_id))
  end
  
  def occupancy_rate(date = Date.current)
    total = total_rooms.to_i.positive? ? total_rooms : rooms.count
    return 0 if total.zero?

    # Count rooms that have checked-in reservations for the given date
    # Note: Room is considered occupied on checkout date until actual checkout
    occupied = rooms.joins(:reservations)
                    .where(reservations: { status: 'checked_in' })
                    .where("reservations.check_in_date <= ? AND reservations.check_out_date >= ?", date, date)
                    .distinct
                    .count
    
    (occupied.to_f / total * 100).round(1)
  end
  
  # Amenity-related methods
  def free_amenities
    amenities.joins(:hotel_amenities)
             .where(hotel_amenities: { is_free: true, is_available: true })
  end
  
  def paid_amenities
    amenities.joins(:hotel_amenities)
             .where(hotel_amenities: { is_free: false, is_available: true })
  end
  
  def available_amenities
    amenities.joins(:hotel_amenities)
             .where(hotel_amenities: { is_available: true })
  end
  
  def amenities_by_category(category)
    amenities.joins(:hotel_amenities)
             .where(hotel_amenities: { is_available: true }, amenities: { category: category })
  end
end
