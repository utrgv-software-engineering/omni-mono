# == Schema Information
#
# Table name: reservations
#
#  id                  :bigint           not null, primary key
#  actual_check_in     :datetime
#  actual_check_out    :datetime
#  adults              :integer          default(1)
#  check_in_date       :date             not null
#  check_out_date      :date             not null
#  children            :integer          default(0)
#  confirmation_number :string           not null
#  currency            :string           default("USD")
#  infants             :integer          default(0)
#  internal_notes      :text
#  paid_amount         :decimal(10, 2)   default(0.0)
#  payment_status      :string           default("pending")
#  source              :string
#  special_requests    :text
#  status              :string           default("pending")
#  total_amount        :decimal(10, 2)   not null
#  created_at          :datetime         not null
#  updated_at          :datetime         not null
#  account_id          :bigint           not null
#  guest_id            :bigint           not null
#  hotel_id            :bigint           not null
#  room_id             :bigint
#  room_type_id        :bigint           not null
#
# Indexes
#
#  idx_on_account_id_hotel_id_check_in_date_7087d23249       (account_id,hotel_id,check_in_date)
#  index_reservations_on_account_id                          (account_id)
#  index_reservations_on_account_id_and_confirmation_number  (account_id,confirmation_number) UNIQUE
#  index_reservations_on_account_id_and_hotel_id_and_status  (account_id,hotel_id,status)
#  index_reservations_on_guest_id                            (guest_id)
#  index_reservations_on_hotel_id                            (hotel_id)
#  index_reservations_on_room_id                             (room_id)
#  index_reservations_on_room_type_id                        (room_type_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (guest_id => guests.id)
#  fk_rails_...  (hotel_id => hotels.id)
#  fk_rails_...  (room_id => rooms.id)
#  fk_rails_...  (room_type_id => room_types.id)
#
class Reservation < ApplicationRecord
  acts_as_tenant(:account)
  belongs_to :account
  belongs_to :hotel
  belongs_to :guest
  belongs_to :room, optional: true # Room can be assigned later
  belongs_to :room_type
  has_many :service_bookings, dependent: :destroy
  
  STATUSES = %w[pending confirmed checked_in checked_out cancelled no_show].freeze
  PAYMENT_STATUSES = %w[pending partial paid refunded].freeze
  SOURCES = %w[walk_in phone website ota].freeze
  
  validates :confirmation_number, presence: true, uniqueness: { scope: :account_id }
  validates :check_in_date, presence: true
  validates :check_out_date, presence: true
  validates :status, inclusion: { in: STATUSES }
  validates :adults, presence: true, numericality: { greater_than: 0 }
  validates :children, numericality: { greater_than_or_equal_to: 0 }
  validates :infants, numericality: { greater_than_or_equal_to: 0 }
  validates :total_amount, presence: true, numericality: { greater_than: 0 }
  validates :paid_amount, numericality: { greater_than_or_equal_to: 0 }
  validates :payment_status, inclusion: { in: PAYMENT_STATUSES }
  validates :source, inclusion: { in: SOURCES }, allow_blank: true
  
  validate :check_out_after_check_in
  validate :room_available_for_dates
  validate :total_occupancy_within_limit
  
  before_validation :generate_confirmation_number, on: :create
  after_create :update_room_status
  after_update :update_room_status_on_change
  
  scope :upcoming, -> { where("check_in_date >= ?", Date.current) }
  scope :past, -> { where("check_out_date < ?", Date.current) }
  scope :current, -> { where("check_in_date <= ? AND check_out_date >= ?", Date.current, Date.current) }
  scope :active, -> { where.not(status: ["cancelled", "no_show"]) }
  scope :by_status, ->(status) { where(status: status) }
  scope :for_date, ->(date) { where("check_in_date <= ? AND check_out_date >= ?", date, date) }
  
  def nights
    (check_out_date - check_in_date).to_i
  end
  
  def nights_count
    nights
  end
  
  def total_guests
    adults + children + infants
  end
  
  def balance_due
    total_amount - paid_amount
  end
  
  def fully_paid?
    paid_amount >= total_amount
  end
  
  # Status helper methods
  def pending?
    status == "pending"
  end
  
  def confirmed?
    status == "confirmed"
  end
  
  def checked_in?
    status == "checked_in"
  end
  
  def checked_out?
    status == "checked_out"
  end
  
  def cancelled?
    status == "cancelled"
  end
  
  def no_show?
    status == "no_show"
  end
  
  def active?
    !cancelled? && !no_show?
  end
  
  # Payment status helper methods
  def payment_pending?
    payment_status == "pending"
  end
  
  def partially_paid?
    payment_status == "partial"
  end
  
  def paid?
    payment_status == "paid"
  end
  
  def refunded?
    payment_status == "refunded"
  end
  
  def can_check_in?
    confirmed? && check_in_date <= Date.current
  end
  
  def can_check_out?
    checked_in?
  end
  
  def check_in!(room_id: nil)
    transaction do
      self.room_id = room_id if room_id.present?
      self.status = "checked_in"
      self.actual_check_in = Time.current
      save!
      room&.update!(status: "occupied")
    end
  end
  
  def check_out!
    transaction do
      self.status = "checked_out"
      self.actual_check_out = Time.current
      save!
      room&.update!(status: "cleaning")
    end
  end
  
  def cancel!
    transaction do
      self.status = "cancelled"
      save!
      room&.update!(status: "available") if room && status_was == "checked_in"
    end
  end
  
  def no_show!
    transaction do
      self.status = "no_show"
      save!
      room&.update!(status: "available") if room
    end
  end
  
  def assign_room(new_room)
    return false unless new_room.available_for_dates?(check_in_date, check_out_date)
    
    update(room: new_room)
  end
  
  def revenue_per_night
    total_amount / nights if nights > 0
  end
  
  def can_modify?
    # Can modify if not checked in/out and at least 24 hours before check-in
    ["pending", "confirmed"].include?(status) && 
      check_in_date > Date.current + 1.day
  end
  
  def can_cancel?
    # Can cancel if not already cancelled/checked out and before check-in
    ["pending", "confirmed"].include?(status) && 
      check_in_date > Date.current
  end
  
  def calculate_cancellation_fee
    # Calculate cancellation fee based on timing
    # This is a simple example - hotels can customize their policies
    return 0 if paid_amount == 0
    
    days_until_checkin = (check_in_date - Date.current).to_i
    
    if days_until_checkin >= 7
      # Free cancellation 7+ days before
      0
    elsif days_until_checkin >= 2
      # 1 night penalty 2-6 days before
      [room_type.base_price, paid_amount].min
    else
      # Full charge less than 48 hours
      paid_amount
    end
  end
  
  def refundable_amount
    paid_amount - calculate_cancellation_fee
  end
  
  private
  
  def generate_confirmation_number
    self.confirmation_number ||= loop do
      random_token = SecureRandom.alphanumeric(8).upcase
      break random_token unless Reservation.exists?(confirmation_number: random_token)
    end
  end
  
  def check_out_after_check_in
    return unless check_in_date && check_out_date
    
    if check_out_date <= check_in_date
      errors.add(:check_out_date, "must be after check-in date")
    end
  end
  
  def room_available_for_dates
    return unless room && check_in_date && check_out_date
    return if ["cancelled", "no_show"].include?(status)
    
    conflicting = Reservation.where(room: room)
                            .where("check_in_date < ? AND check_out_date > ?", check_out_date, check_in_date)
                            .where.not(status: ["cancelled", "no_show"])
                            .where.not(id: id)
    
    if conflicting.exists?
      errors.add(:room, "is not available for the selected dates")
    end
  end
  
  def total_occupancy_within_limit
    return unless room_type && adults && children
    
    total = adults + children
    if total > room_type.max_occupancy
      errors.add(:base, "Total guests exceed room capacity of #{room_type.max_occupancy}")
    end
  end
  
  def update_room_status
    room&.update!(status: "occupied") if status == "checked_in"
  end
  
  def update_room_status_on_change
    if saved_change_to_status?
      case status
      when "checked_in"
        room&.update!(status: "occupied")
      when "checked_out"
        room&.update!(status: "cleaning")
      when "cancelled", "no_show"
        room&.update!(status: "available") if status_before_last_save == "checked_in"
      end
    end
  end
end
