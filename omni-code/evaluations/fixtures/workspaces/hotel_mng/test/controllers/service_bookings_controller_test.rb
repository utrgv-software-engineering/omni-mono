# frozen_string_literal: true

require "test_helper"

class ServiceBookingsControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @guest = create(:guest, account: @account)
    @service = create(:service, account: @account, hotel: @hotel, name: "Spa Treatment", category: "spa")
    @reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)
    @service_booking = create(:service_booking,
                               account: @account,
                               service: @service,
                               guest: @guest,
                               reservation: @reservation,
                               unit_price: 50.00,
                               total_price: 50.00)
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping cannot access service_bookings index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/service_bookings"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can access service_bookings index" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/service_bookings"
    assert_response :success
  end

  test "front_desk can view service_booking details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/service_bookings/#{@service_booking.id}"
    assert_response :success
  end

  test "front_desk can create service_booking" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_difference "ServiceBooking.count", 1 do
      post "/service_bookings", params: {
        service_booking: {
          service_id: @service.id,
          guest_id: @guest.id,
          booking_date: Date.tomorrow,
          unit_price: 100.00,
          total_price: 100.00
        }
      }
    end

    booking = ServiceBooking.last
    assert_redirected_to "/service_bookings/#{booking.id}"
  end

  test "front_desk can update service_booking" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/service_bookings/#{@service_booking.id}", params: {
      service_booking: { notes: "VIP guest", quantity: 2 }
    }

    assert_redirected_to "/service_bookings/#{@service_booking.id}"
    @service_booking.reload
    assert_equal "VIP guest", @service_booking.notes
    assert_equal 2, @service_booking.quantity
  end

  test "front_desk cannot destroy service_booking" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "ServiceBooking.count" do
      delete "/service_bookings/#{@service_booking.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager can destroy service_booking" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "ServiceBooking.count", -1 do
      delete "/service_bookings/#{@service_booking.id}"
    end

    assert_redirected_to "/service_bookings"
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns service_bookings list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/service_bookings"
    assert_response :success
  end

  test "show displays service_booking details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/service_bookings/#{@service_booking.id}"
    assert_response :success
  end

  test "new displays service_booking form" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/service_bookings/new"
    assert_response :success
    assert_select "form[action='/service_bookings']"
  end

  test "create service_booking with valid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_difference "ServiceBooking.count", 1 do
      post "/service_bookings", params: {
        service_booking: {
          service_id: @service.id,
          guest_id: @guest.id,
          reservation_id: @reservation.id,
          booking_date: Date.tomorrow,
          booking_time: "14:00",
          quantity: 2,
          unit_price: 50.00,
          total_price: 100.00,
          duration_minutes: 60,
          notes: "Special request"
        }
      }
    end

    booking = ServiceBooking.last
    assert_redirected_to "/service_bookings/#{booking.id}"
    assert_equal @service, booking.service
    assert_equal @guest, booking.guest
    assert_equal @reservation, booking.reservation
    assert_equal @account, booking.account
  end

  test "create service_booking with invalid params fails" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "ServiceBooking.count" do
      post "/service_bookings", params: {
        service_booking: {
          service_id: @service.id,
          guest_id: @guest.id,
          booking_date: nil, # Required
          unit_price: 50.00,
          total_price: 50.00
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "edit displays service_booking form" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/service_bookings/#{@service_booking.id}/edit"
    assert_response :success
    assert_select "form[action='/service_bookings/#{@service_booking.id}']"
  end

  test "update service_booking with valid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/service_bookings/#{@service_booking.id}", params: {
      service_booking: {
        status: "confirmed",
        notes: "Confirmed for VIP"
      }
    }

    assert_redirected_to "/service_bookings/#{@service_booking.id}"
    @service_booking.reload
    assert_equal "confirmed", @service_booking.status
    assert_equal "Confirmed for VIP", @service_booking.notes
  end

  # ===========================================
  # Nested under Reservation Tests
  # ===========================================

  test "front_desk can access service_bookings nested under reservation" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/reservations/#{@reservation.id}/service_bookings"
    assert_response :success
  end

  test "front_desk can create service_booking nested under reservation" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_difference "ServiceBooking.count", 1 do
      post "/reservations/#{@reservation.id}/service_bookings", params: {
        service_booking: {
          service_id: @service.id,
          guest_id: @guest.id,
          booking_date: Date.tomorrow,
          unit_price: 75.00,
          total_price: 75.00
        }
      }
    end

    booking = ServiceBooking.last
    assert_redirected_to "/reservations/#{@reservation.id}/service_bookings/#{booking.id}"
    assert_equal @reservation, booking.reservation
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access service_booking from another account" do
    other_account = create(:account)
    other_booking = nil

    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_guest = create(:guest, account: other_account)
      other_service = create(:service, account: other_account, hotel: other_hotel)
      other_booking = create(:service_booking, :without_reservation,
                              account: other_account,
                              service: other_service,
                              guest: other_guest)
    end

    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/service_bookings/#{other_booking.id}"
    assert_response :not_found
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can perform all service_booking operations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Index
    get "/service_bookings"
    assert_response :success

    # Show
    get "/service_bookings/#{@service_booking.id}"
    assert_response :success

    # Create
    assert_difference "ServiceBooking.count", 1 do
      post "/service_bookings", params: {
        service_booking: {
          service_id: @service.id,
          guest_id: @guest.id,
          booking_date: Date.tomorrow,
          unit_price: 60.00,
          total_price: 60.00
        }
      }
    end

    # Update
    patch "/service_bookings/#{@service_booking.id}", params: {
      service_booking: { notes: "Admin updated" }
    }
    assert_redirected_to "/service_bookings/#{@service_booking.id}"

    # Destroy
    assert_difference "ServiceBooking.count", -1 do
      delete "/service_bookings/#{@service_booking.id}"
    end
  end

  test "unauthenticated user cannot access service_bookings" do
    get "/service_bookings"
    assert_response :not_found
  end
end
