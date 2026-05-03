# == Schema Information
#
# Table name: service_schedules
#
#  id                    :bigint           not null, primary key
#  day_of_week           :integer          not null
#  end_time              :time             not null
#  max_bookings_per_slot :integer
#  start_time            :time             not null
#  created_at            :datetime         not null
#  updated_at            :datetime         not null
#  account_id            :bigint           not null
#  service_id            :bigint           not null
#
# Indexes
#
#  idx_on_account_id_service_id_day_of_week_fa26cada02  (account_id,service_id,day_of_week)
#  index_service_schedules_on_account_id                (account_id)
#  index_service_schedules_on_service_id                (service_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (service_id => services.id)
#

class ServiceSchedule < ApplicationRecord
  acts_as_tenant(:account)
  belongs_to :account
  belongs_to :service

  DAYS_OF_WEEK = {
    0 => 'Sunday',
    1 => 'Monday',
    2 => 'Tuesday',
    3 => 'Wednesday',
    4 => 'Thursday',
    5 => 'Friday',
    6 => 'Saturday'
  }.freeze

  validates :day_of_week, presence: true, inclusion: { in: 0..6 }
  validates :start_time, presence: true
  validates :end_time, presence: true
  validates :max_bookings_per_slot, numericality: { greater_than: 0 }, allow_nil: true

  validate :end_time_after_start_time
  validate :unique_day_for_service

  scope :for_day, ->(day) { where(day_of_week: day) }
  scope :ordered, -> { order(:day_of_week, :start_time) }

  def day_name
    DAYS_OF_WEEK[day_of_week]
  end

  def formatted_start_time
    start_time.strftime('%l:%M %p').strip if start_time
  end

  def formatted_end_time
    end_time.strftime('%l:%M %p').strip if end_time
  end

  def formatted_hours
    "#{formatted_start_time} - #{formatted_end_time}"
  end

  def duration_in_minutes
    return 0 unless start_time && end_time
    ((end_time - start_time) / 60).to_i
  end

  def duration_in_hours
    duration_in_minutes / 60.0
  end

  def overlaps_with?(other_schedule)
    return false if other_schedule.day_of_week != day_of_week
    
    start_time < other_schedule.end_time && end_time > other_schedule.start_time
  end

  def includes_time?(time)
    # Convert both times to seconds since midnight for comparison
    test_seconds = if time.is_a?(String)
      Time.parse(time).seconds_since_midnight
    else
      time.seconds_since_midnight
    end
    
    start_seconds = start_time.seconds_since_midnight
    end_seconds = end_time.seconds_since_midnight
    
    test_seconds >= start_seconds && test_seconds <= end_seconds
  end

  def available_slots(date, slot_duration_minutes = 30)
    return [] unless date.wday == day_of_week

    slots = []
    current_time = start_time

    while current_time + slot_duration_minutes.minutes <= end_time
      slots << {
        start_time: current_time,
        end_time: current_time + slot_duration_minutes.minutes,
        available: true
      }
      current_time += slot_duration_minutes.minutes
    end

    slots
  end

  private

  def end_time_after_start_time
    return unless start_time && end_time

    if end_time <= start_time
      errors.add(:end_time, 'must be after start time')
    end
  end

  def unique_day_for_service
    return unless service && day_of_week

    existing = ServiceSchedule.where(service: service, day_of_week: day_of_week)
    existing = existing.where.not(id: id) if persisted?

    if existing.exists?
      errors.add(:day_of_week, 'already has a schedule for this service')
    end
  end
end
