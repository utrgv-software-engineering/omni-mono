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
require "test_helper"

class ServiceTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @service = create(:service, account: @account, hotel: @hotel)
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  test "calculate_price for fixed pricing" do
    service = create(:service, pricing_unit: 'fixed', price: 100)
    assert_equal 100, service.calculate_price(3)
  end

  test "calculate_price for per_person pricing" do
    service = create(:service, pricing_unit: 'per_person', price: 50)
    assert_equal 150, service.calculate_price(3)
  end

  test "calculate_price for per_item pricing" do
    service = create(:service, pricing_unit: 'per_item', price: 10)
    assert_equal 50, service.calculate_price(5)
  end

  test "calculate_price for per_hour pricing with duration" do
    service = create(:service, pricing_unit: 'per_hour', price: 100, duration_minutes: 90)
    assert_equal 150, service.calculate_price(1)
  end

  test "calculate_price for per_hour pricing with custom duration" do
    service = create(:service, pricing_unit: 'per_hour', price: 100)
    assert_equal 250, service.calculate_price(1, 2.5)
  end

  test "available_on? returns true when no schedule exists" do
    assert @service.available_on?(1)
  end

  test "available_on? returns false when schedule exists but not for that day" do
    create(:service_schedule, service: @service, day_of_week: 1)
    assert @service.available_on?(1)
    assert_not @service.available_on?(2)
  end

  test "available_slots_on_date calculates remaining slots" do
    service = create(:service)
    schedule = create(:service_schedule, service: service, day_of_week: 1, max_bookings_per_slot: 5)

    # Get next Monday (must be in the future for booking validation)
    date = Date.current.next_occurring(:monday)

    create(:service_booking, service: service, booking_date: date, status: 'confirmed')
    create(:service_booking, service: service, booking_date: date, status: 'pending')

    assert_equal 3, service.available_slots_on_date(date)
  end

  test "available_slots_on_date returns nil when no max_bookings_per_slot" do
    service = create(:service)
    schedule = create(:service_schedule, service: service, day_of_week: 1, max_bookings_per_slot: nil)
    
    date = Date.current.beginning_of_week + 1.day
    
    assert_nil service.available_slots_on_date(date)
  end

  test "available_slots_on_date ignores cancelled bookings" do
    service = create(:service)
    schedule = create(:service_schedule, service: service, day_of_week: 1, max_bookings_per_slot: 5)

    # Get next Monday (must be in the future for booking validation)
    date = Date.current.next_occurring(:monday)

    create(:service_booking, service: service, booking_date: date, status: 'confirmed')
    create(:service_booking, service: service, booking_date: date, status: 'cancelled')

    assert_equal 4, service.available_slots_on_date(date)
  end
end
