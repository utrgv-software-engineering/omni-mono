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

require "test_helper"

class ServiceScheduleTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @service = create(:service, account: @account, hotel: @hotel)
    @schedule = create(:service_schedule, account: @account, service: @service)
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  test "end_time must be after start_time" do
    schedule = build(:service_schedule, start_time: "14:00", end_time: "13:00")
    assert_not schedule.valid?
    assert_includes schedule.errors[:end_time], "must be after start time"
  end

  test "cannot have duplicate schedules for same day and service" do
    existing = create(:service_schedule, service: @service, day_of_week: 2)
    duplicate = build(:service_schedule, service: @service, day_of_week: 2)
    
    assert_not duplicate.valid?
    assert_includes duplicate.errors[:day_of_week], "already has a schedule for this service"
  end

  test "duration_in_minutes calculates correctly" do
    schedule = create(:service_schedule, start_time: "09:00", end_time: "11:30")
    assert_equal 150, schedule.duration_in_minutes
  end

  test "duration_in_hours calculates correctly" do
    schedule = create(:service_schedule, start_time: "09:00", end_time: "11:30")
    assert_equal 2.5, schedule.duration_in_hours
  end

  test "overlaps_with? detects overlapping schedules" do
    schedule1 = create(:service_schedule, day_of_week: 1, start_time: "09:00", end_time: "12:00")
    schedule2 = build(:service_schedule, day_of_week: 1, start_time: "11:00", end_time: "14:00")
    schedule3 = build(:service_schedule, day_of_week: 1, start_time: "12:00", end_time: "14:00")
    schedule4 = build(:service_schedule, day_of_week: 2, start_time: "10:00", end_time: "11:00")
    
    assert schedule1.overlaps_with?(schedule2)
    assert_not schedule1.overlaps_with?(schedule3)
    assert_not schedule1.overlaps_with?(schedule4)
  end

  test "includes_time? checks if time is within schedule" do
    schedule = create(:service_schedule, start_time: "09:00", end_time: "17:00")
    
    assert schedule.includes_time?(Time.parse("12:00"))
    assert schedule.includes_time?(Time.parse("09:00"))
    assert schedule.includes_time?(Time.parse("17:00"))
    assert_not schedule.includes_time?(Time.parse("17:01"))
    assert_not schedule.includes_time?(Time.parse("08:59"))
  end

  test "available_slots generates correct time slots" do
    schedule = create(:service_schedule, 
      day_of_week: 1, 
      start_time: "09:00", 
      end_time: "11:00"
    )
    
    # Get the next Monday
    monday = Date.current.beginning_of_week
    monday += 1.day until monday.wday == 1
    
    slots = schedule.available_slots(monday, 30)
    
    assert_equal 4, slots.length
    assert_equal schedule.start_time, slots[0][:start_time]
    assert_equal schedule.start_time + 30.minutes, slots[0][:end_time]
    assert_equal schedule.start_time + 90.minutes, slots[3][:start_time]
    assert_equal schedule.end_time, slots[3][:end_time]
  end

  test "available_slots returns empty array for wrong day" do
    schedule = create(:service_schedule, day_of_week: 1)
    tuesday = Date.current.beginning_of_week + 1.day
    
    assert_empty schedule.available_slots(tuesday, 30)
  end

  test "day_name returns correct day string" do
    schedule = create(:service_schedule, day_of_week: 0)
    assert_equal "Sunday", schedule.day_name
    
    schedule.day_of_week = 6
    assert_equal "Saturday", schedule.day_name
  end

  test "formatted times return proper format" do
    schedule = create(:service_schedule, start_time: "09:00", end_time: "17:30")
    
    assert_equal "9:00 AM", schedule.formatted_start_time
    assert_equal "5:30 PM", schedule.formatted_end_time
    assert_equal "9:00 AM - 5:30 PM", schedule.formatted_hours
  end
end
