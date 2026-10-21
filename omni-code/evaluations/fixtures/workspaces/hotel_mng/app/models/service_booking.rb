# == Schema Information
#
# Table name: service_bookings
#
#  id               :bigint           not null, primary key
#  booking_date     :date             not null
#  booking_time     :time
#  duration_minutes :integer
#  notes            :text
#  quantity         :integer          default(1)
#  status           :string           default("pending")
#  total_price      :decimal(10, 2)   not null
#  unit_price       :decimal(10, 2)   not null
#  created_at       :datetime         not null
#  updated_at       :datetime         not null
#  account_id       :bigint           not null
#  guest_id         :bigint           not null
#  reservation_id   :bigint
#  service_id       :bigint           not null
#
# Indexes
#
#  idx_on_account_id_booking_date_status_e0ad81b3d5      (account_id,booking_date,status)
#  idx_on_account_id_service_id_booking_date_e8b5f59c22  (account_id,service_id,booking_date)
#  index_service_bookings_on_account_id                  (account_id)
#  index_service_bookings_on_guest_id                    (guest_id)
#  index_service_bookings_on_reservation_id              (reservation_id)
#  index_service_bookings_on_service_id                  (service_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (guest_id => guests.id)
#  fk_rails_...  (reservation_id => reservations.id)
#  fk_rails_...  (service_id => services.id)
#
class ServiceBooking < ApplicationRecord
  acts_as_tenant(:account)
  belongs_to :account
  belongs_to :reservation, optional: true
  belongs_to :service
  belongs_to :guest

  STATUSES = %w[pending confirmed completed cancelled].freeze

  validates :booking_date, presence: true
  validates :quantity, presence: true, numericality: { greater_than: 0 }
  validates :unit_price, presence: true, numericality: { greater_than_or_equal_to: 0 }
  validates :total_price, presence: true, numericality: { greater_than_or_equal_to: 0 }
  validates :status, inclusion: { in: STATUSES }
  validates :duration_minutes, numericality: { greater_than: 0 }, allow_nil: true

  validate :booking_date_not_in_past, on: :create
  validate :service_available_on_date
  validate :total_price_matches_calculation

  before_validation :calculate_total_price, if: -> { unit_price.present? && quantity.present? }

  scope :upcoming, -> { where('booking_date >= ?', Date.current) }
  scope :past, -> { where('booking_date < ?', Date.current) }
  scope :by_status, ->(status) { where(status: status) }
  scope :for_date, ->(date) { where(booking_date: date) }
  scope :for_service, ->(service) { where(service: service) }

  def pending?
    status == 'pending'
  end

  def confirmed?
    status == 'confirmed'
  end

  def completed?
    status == 'completed'
  end

  def cancelled?
    status == 'cancelled'
  end

  def upcoming?
    booking_date >= Date.current
  end

  def past?
    booking_date < Date.current
  end

  def can_cancel?
    ['pending', 'confirmed'].include?(status) && upcoming?
  end

  def cancel!
    update!(status: 'cancelled') if can_cancel?
  end

  def confirm!
    update!(status: 'confirmed') if pending?
  end

  def complete!
    update!(status: 'completed') if confirmed?
  end

  def formatted_booking_time
    return nil unless booking_time
    booking_time.strftime('%l:%M %p').strip
  end

  def end_time
    return nil unless booking_time && duration_minutes
    (booking_time.to_datetime + duration_minutes.minutes).strftime('%l:%M %p').strip
  end

  private

  def calculate_total_price
    return unless service

    calculated_price = case service.pricing_unit
    when 'per_hour'
      hours = duration_minutes.to_f / 60
      unit_price * quantity * hours
    when 'per_person', 'per_item'
      unit_price * quantity
    else
      unit_price
    end

    self.total_price = calculated_price
  end

  def booking_date_not_in_past
    return unless booking_date.present?
    
    if booking_date < Date.current
      errors.add(:booking_date, "can't be in the past")
    end
  end

  def service_available_on_date
    return unless service && booking_date

    unless service.available?
      errors.add(:service, 'is not currently available')
      return
    end

    if service.has_schedule? && !service.available_on?(booking_date.wday)
      errors.add(:booking_date, 'service is not available on this day')
    end
  end

  def total_price_matches_calculation
    return unless unit_price && quantity && total_price && service

    expected_total = case service.pricing_unit
    when 'per_hour'
      hours = (duration_minutes || service.duration_minutes || 60).to_f / 60
      unit_price * quantity * hours
    when 'per_person', 'per_item'
      unit_price * quantity
    else
      unit_price
    end

    if (total_price - expected_total).abs > 0.01
      errors.add(:total_price, "doesn't match calculated price")
    end
  end
end
