# == Schema Information
#
# Table name: rooms
#
#  id              :bigint           not null, primary key
#  floor           :string
#  is_smoking      :boolean          default(FALSE)
#  last_cleaned_at :datetime
#  notes           :text
#  room_number     :string           not null
#  status          :string           default("available")
#  created_at      :datetime         not null
#  updated_at      :datetime         not null
#  account_id      :bigint           not null
#  hotel_id        :bigint           not null
#  room_type_id    :bigint           not null
#
# Indexes
#
#  index_rooms_on_account_id                          (account_id)
#  index_rooms_on_account_id_and_hotel_id_and_status  (account_id,hotel_id,status)
#  index_rooms_on_hotel_id                            (hotel_id)
#  index_rooms_on_hotel_id_and_room_number            (hotel_id,room_number) UNIQUE
#  index_rooms_on_room_type_id                        (room_type_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#  fk_rails_...  (room_type_id => room_types.id)
#
class Room < ApplicationRecord
  acts_as_tenant(:account)
  belongs_to :account
  belongs_to :hotel
  belongs_to :room_type
  has_many :reservations
  has_many :housekeeping_tasks
  has_many :maintenance_requests
  
  STATUSES = %w[available occupied maintenance cleaning].freeze
  
  validates :room_number, presence: true, uniqueness: { scope: :hotel_id }
  validates :status, inclusion: { in: STATUSES }
  validates :is_smoking, inclusion: { in: [true, false] }
  
  scope :available, -> { where(status: "available") }
  scope :occupied, -> { where(status: "occupied") }
  scope :maintenance, -> { where(status: "maintenance") }
  scope :cleaning, -> { where(status: "cleaning") }
  scope :smoking, -> { where(is_smoking: true) }
  scope :non_smoking, -> { where(is_smoking: false) }
  scope :on_floor, ->(floor) { where(floor: floor) }
  
  def available?
    status == "available"
  end
  
  def occupied?
    status == "occupied"
  end
  
  def under_maintenance?
    status == "maintenance"
  end
  
  def being_cleaned?
    status == "cleaning"
  end
  
  def available_for_dates?(check_in_date, check_out_date)
    return false unless available?
    
    !reservations.where("check_in_date < ? AND check_out_date > ?", check_out_date, check_in_date)
                 .where.not(status: ["cancelled", "no_show"])
                 .exists?
  end
  
  def current_reservation
    reservations.where(status: "checked_in")
                .where("check_in_date <= ? AND check_out_date >= ?", Date.current, Date.current)
                .first
  end
  
  def current_guest
    current_reservation&.guest
  end
  
  def upcoming_reservation
    reservations.where(status: ["pending", "confirmed"])
                .where("check_in_date > ?", Date.current)
                .order(:check_in_date)
                .first
  end
  
  def upcoming_guest
    upcoming_reservation&.guest
  end
  
  def mark_as_cleaned!
    update!(status: "available", last_cleaned_at: Time.current)
  end
  
  def needs_cleaning?
    return true if being_cleaned?
    return false if last_cleaned_at.nil?
    
    last_cleaned_at < 24.hours.ago
  end
  
  def occupancy_percentage
    return 0 unless room_type
    
    total_guests = current_reservation ? (current_reservation.adults + current_reservation.children) : 0
    (total_guests.to_f / room_type.max_occupancy * 100).round
  end
  
  def next_available_date
    return Date.current if available?
    
    if current_reservation
      current_reservation.check_out_date
    else
      nil
    end
  end
  
  def days_since_last_cleaned
    return nil if last_cleaned_at.nil?
    ((Time.current - last_cleaned_at) / 1.day).round
  end
  
  def display_name
    "Room #{room_number}"
  end
end
