# == Schema Information
#
# Table name: services
#
#  id                       :bigint           not null, primary key
#  advance_booking_required :boolean          default(FALSE)
#  capacity                 :integer
#  category                 :string           not null
#  description              :text
#  duration_minutes         :integer
#  is_active                :boolean          default(TRUE)
#  name                     :string           not null
#  price                    :decimal(10, 2)   not null
#  pricing_unit             :string           default("fixed")
#  created_at               :datetime         not null
#  updated_at               :datetime         not null
#  account_id               :bigint           not null
#  hotel_id                 :bigint           not null
#
# Indexes
#
#  index_services_on_account_id                             (account_id)
#  index_services_on_account_id_and_hotel_id_and_is_active  (account_id,hotel_id,is_active)
#  index_services_on_hotel_id                               (hotel_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
class Service < ApplicationRecord
  acts_as_tenant(:account)
  belongs_to :account
  belongs_to :hotel
  has_many :service_bookings, dependent: :destroy
  has_many :service_schedules, dependent: :destroy

  CATEGORIES = %w[spa dining transport laundry other].freeze
  PRICING_UNITS = %w[fixed per_person per_hour per_item].freeze

  validates :name, presence: true
  validates :category, presence: true, inclusion: { in: CATEGORIES }
  validates :price, presence: true, numericality: { greater_than_or_equal_to: 0 }
  validates :pricing_unit, inclusion: { in: PRICING_UNITS }
  validates :duration_minutes, numericality: { greater_than: 0 }, allow_nil: true
  validates :capacity, numericality: { greater_than: 0 }, allow_nil: true
  validates :advance_booking_required, inclusion: { in: [true, false] }
  validates :is_active, inclusion: { in: [true, false] }

  scope :active, -> { where(is_active: true) }
  scope :by_category, ->(category) { where(category: category) }
  scope :requires_booking, -> { where(advance_booking_required: true) }
  scope :walk_in, -> { where(advance_booking_required: false) }

  def available?
    is_active
  end

  def calculate_price(quantity = 1, duration_hours = nil)
    case pricing_unit
    when 'fixed'
      price
    when 'per_person', 'per_item'
      price * quantity
    when 'per_hour'
      hours = duration_hours || (duration_minutes.to_f / 60)
      price * hours
    else
      price
    end
  end

  def has_schedule?
    service_schedules.any?
  end

  def available_on?(day_of_week)
    return true unless has_schedule?
    service_schedules.where(day_of_week: day_of_week).exists?
  end

  def schedule_for_day(day_of_week)
    service_schedules.find_by(day_of_week: day_of_week)
  end

  def bookings_on_date(date)
    service_bookings.where(booking_date: date)
  end

  def available_slots_on_date(date)
    return nil unless has_schedule?
    
    schedule = schedule_for_day(date.wday)
    return nil unless schedule

    existing_bookings = bookings_on_date(date).where(status: ['pending', 'confirmed'])
    
    if schedule.max_bookings_per_slot
      schedule.max_bookings_per_slot - existing_bookings.count
    else
      nil
    end
  end
end
