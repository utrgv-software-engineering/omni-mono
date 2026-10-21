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
require "test_helper"

class ServiceBookingTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @service = create(:service, account: @account, hotel: @hotel)
    @guest = create(:guest, account: @account)
    @booking = create(:service_booking, account: @account, service: @service, guest: @guest)
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  test "calculate total price for per_person service" do
    service = create(:service, pricing_unit: 'per_person', price: 50)
    booking = build(:service_booking, service: service, unit_price: 50, quantity: 3)
    booking.valid?
    assert_equal 150, booking.total_price
  end

  test "calculate total price for per_hour service" do
    service = create(:service, pricing_unit: 'per_hour', price: 100)
    booking = build(:service_booking, service: service, unit_price: 100, quantity: 1, duration_minutes: 90)
    booking.valid?
    assert_equal 150, booking.total_price
  end

  test "calculate total price for fixed service" do
    service = create(:service, pricing_unit: 'fixed', price: 200)
    booking = build(:service_booking, service: service, unit_price: 200, quantity: 3)
    booking.valid?
    assert_equal 200, booking.total_price
  end

  test "cannot create booking for past date" do
    booking = build(:service_booking, booking_date: Date.current - 1.day)
    assert_not booking.valid?
    assert_includes booking.errors[:booking_date], "can't be in the past"
  end

  test "cannot book inactive service" do
    inactive_service = create(:service, is_active: false)
    booking = build(:service_booking, service: inactive_service)
    assert_not booking.valid?
    assert_includes booking.errors[:service], "is not currently available"
  end

  test "validates service availability on scheduled day" do
    service = create(:service)
    create(:service_schedule, service: service, day_of_week: 1)
    
    tuesday = Date.current.beginning_of_week + 2.days
    booking = build(:service_booking, service: service, booking_date: tuesday)
    
    assert_not booking.valid?
    assert_includes booking.errors[:booking_date], "service is not available on this day"
  end

  test "can cancel upcoming bookings" do
    future_booking = create(:service_booking, booking_date: Date.current + 1.day, status: 'confirmed')
    assert future_booking.can_cancel?
    future_booking.cancel!
    assert_equal 'cancelled', future_booking.status
  end

  test "cannot cancel past bookings" do
    # Create a service booking with a past date by skipping validation
    past_booking = build(:service_booking, booking_date: Date.current - 1.day, status: 'completed')
    past_booking.save(validate: false)
    assert_not past_booking.can_cancel?
  end

  test "state transitions work correctly" do
    booking = create(:service_booking, status: 'pending')
    
    booking.confirm!
    assert_equal 'confirmed', booking.status
    
    booking.complete!
    assert_equal 'completed', booking.status
  end

  test "formatted_booking_time returns proper format" do
    booking = create(:service_booking, booking_time: "14:30")
    assert_equal "2:30 PM", booking.formatted_booking_time
  end

  test "end_time calculates correctly" do
    booking = create(:service_booking, booking_time: "14:00", duration_minutes: 90)
    assert_equal "3:30 PM", booking.end_time
  end

  test "validates total price matches calculation" do
    service = create(:service, pricing_unit: 'per_person', price: 50)
    booking = build(:service_booking, 
      service: service, 
      unit_price: 50, 
      quantity: 2
    )
    
    # Skip the callback and set wrong total
    booking.instance_eval { def calculate_total_price; end }
    booking.total_price = 75
    
    assert_not booking.valid?
    assert_includes booking.errors[:total_price], "doesn't match calculated price"
  end
end
