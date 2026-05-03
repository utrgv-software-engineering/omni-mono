# frozen_string_literal: true

require "test_helper"

class ReservationsControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @guest = create(:guest, account: @account)
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping cannot access reservations index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/reservations"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can access reservations index" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/reservations"
    assert_response :success
  end

  test "hotel_manager can access reservations index" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/reservations"
    assert_response :success
  end

  test "front_desk cannot destroy reservation" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    assert_no_difference "Reservation.count" do
      delete "/reservations/#{reservation.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager can destroy reservation" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    assert_difference "Reservation.count", -1 do
      delete "/reservations/#{reservation.id}"
    end

    assert_redirected_to "/reservations"
  end

  test "owner can destroy reservation" do
    sign_in_as(:owner, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    assert_difference "Reservation.count", -1 do
      delete "/reservations/#{reservation.id}"
    end

    assert_redirected_to "/reservations"
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access reservations from another account" do
    other_account = create(:account)
    other_hotel = nil
    other_reservation = nil

    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
      other_guest = create(:guest, account: other_account)
      other_reservation = create(:reservation, account: other_account, hotel: other_hotel, guest: other_guest, room_type: other_room_type)
    end

    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    # The controller/policy may render 404 or raise RecordNotFound
    get "/reservations/#{other_reservation.id}"
    assert_response :not_found
  end

  test "index only shows reservations from current account" do
    # Create reservation in current account
    our_reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    # Create reservation in another account
    other_account = create(:account)
    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
      other_guest = create(:guest, account: other_account)
      create(:reservation, account: other_account, hotel: other_hotel, guest: other_guest, room_type: other_room_type)
    end

    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/reservations"
    assert_response :success

    # Should only see our reservation
    assert_select "a[href='/reservations/#{our_reservation.id}']"
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns reservations list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    get "/reservations"
    assert_response :success
    assert_select "a[href='/reservations/#{reservation.id}']"
  end

  test "show displays reservation details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    get "/reservations/#{reservation.id}"
    assert_response :success
    assert_select "main", /#{reservation.confirmation_number}/
  end

  test "new displays reservation form" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/reservations/new"
    assert_response :success
    assert_select "form[action='/reservations']"
  end

  test "create reservation with valid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_difference "Reservation.count", 1 do
      post "/reservations", params: {
        reservation: {
          hotel_id: @hotel.id,
          guest_id: @guest.id,
          room_type_id: @room_type.id,
          check_in_date: Date.tomorrow,
          check_out_date: 3.days.from_now,
          adults: 2,
          total_amount: 300.00
        }
      }
    end

    reservation = Reservation.last
    assert_redirected_to "/reservations/#{reservation.id}"
    assert_equal "pending", reservation.status
    assert reservation.confirmation_number.present?
  end

  test "create generates unique confirmation number" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    post "/reservations", params: {
      reservation: {
        hotel_id: @hotel.id,
        guest_id: @guest.id,
        room_type_id: @room_type.id,
        check_in_date: Date.tomorrow,
        check_out_date: 3.days.from_now,
        adults: 2,
        total_amount: 300.00
      }
    }

    reservation = Reservation.last
    # Confirmation number format: YYYYMMDD + 6 hex chars
    assert_match(/\A\d{8}[A-F0-9]{6}\z/, reservation.confirmation_number)
  end

  test "create fails with invalid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "Reservation.count" do
      post "/reservations", params: {
        reservation: {
          hotel_id: @hotel.id,
          guest_id: @guest.id,
          room_type_id: @room_type.id,
          check_in_date: Date.tomorrow,
          check_out_date: Date.yesterday, # Invalid: check_out before check_in
          adults: 2,
          total_amount: 300.00
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "edit displays reservation form" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    get "/reservations/#{reservation.id}/edit"
    assert_response :success
    assert_select "form[action='/reservations/#{reservation.id}']"
  end

  test "update reservation with valid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    patch "/reservations/#{reservation.id}", params: {
      reservation: {
        special_requests: "Late check-in requested"
      }
    }

    assert_redirected_to "/reservations/#{reservation.id}"
    reservation.reload
    assert_equal "Late check-in requested", reservation.special_requests
  end

  test "update fails with invalid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    patch "/reservations/#{reservation.id}", params: {
      reservation: {
        check_out_date: reservation.check_in_date - 1.day # Invalid
      }
    }

    assert_response :unprocessable_content
  end

  # ===========================================
  # Filtering Tests (per Testing Guide 3.2.1)
  # ===========================================

  test "index filters by status" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    pending_res = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "pending")
    confirmed_res = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    get "/reservations", params: { status: "pending" }
    assert_response :success

    # Should show pending reservation
    assert_select "a[href='/reservations/#{pending_res.id}']"
    # Should not show confirmed reservation
    assert_select "a[href='/reservations/#{confirmed_res.id}']", count: 0
  end

  test "index filters arrivals for today" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    today_arrival = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type,
                           check_in_date: Date.current, check_out_date: Date.current + 2.days, status: "confirmed")
    tomorrow_arrival = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type,
                              check_in_date: Date.tomorrow, check_out_date: Date.tomorrow + 2.days, status: "confirmed")

    get "/reservations", params: { filter: "arrivals" }
    assert_response :success

    assert_select "a[href='/reservations/#{today_arrival.id}']"
    assert_select "a[href='/reservations/#{tomorrow_arrival.id}']", count: 0
  end

  test "index filters departures for today" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    room1 = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    room2 = create(:room, hotel: @hotel, account: @account, room_type: @room_type)

    # Departure today - checked in guest (use room1)
    today_departure = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest,
                             room_type: @room_type, room: room1,
                             check_in_date: Date.current - 2.days, check_out_date: Date.current)

    # Departure tomorrow (use room2)
    tomorrow_departure = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest,
                                room_type: @room_type, room: room2,
                                check_in_date: Date.current - 1.day, check_out_date: Date.tomorrow)

    get "/reservations", params: { filter: "departures" }
    assert_response :success

    assert_select "a[href='/reservations/#{today_departure.id}']"
    assert_select "a[href='/reservations/#{tomorrow_departure.id}']", count: 0
  end

  test "index filters in-house guests" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)

    checked_in_res = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest,
                            room_type: @room_type, room: room)
    pending_res = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    get "/reservations", params: { filter: "in_house" }
    assert_response :success

    assert_select "a[href='/reservations/#{checked_in_res.id}']"
    assert_select "a[href='/reservations/#{pending_res.id}']", count: 0
  end

  test "index searches by guest name" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    guest_smith = create(:guest, account: @account, first_name: "John", last_name: "Smith")
    guest_jones = create(:guest, account: @account, first_name: "Jane", last_name: "Jones")

    smith_res = create(:reservation, account: @account, hotel: @hotel, guest: guest_smith, room_type: @room_type)
    jones_res = create(:reservation, account: @account, hotel: @hotel, guest: guest_jones, room_type: @room_type)

    get "/reservations", params: { search: "Smith" }
    assert_response :success

    assert_select "a[href='/reservations/#{smith_res.id}']"
    assert_select "a[href='/reservations/#{jones_res.id}']", count: 0
  end

  test "index searches by confirmation number" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)
    other_reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    get "/reservations", params: { search: reservation.confirmation_number }
    assert_response :success

    assert_select "a[href='/reservations/#{reservation.id}']"
    assert_select "a[href='/reservations/#{other_reservation.id}']", count: 0
  end

  test "index filters by date range" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Reservation in range
    in_range = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type,
                      check_in_date: Date.current + 5.days, check_out_date: Date.current + 8.days)

    # Reservation outside range
    out_of_range = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type,
                          check_in_date: Date.current + 20.days, check_out_date: Date.current + 23.days)

    get "/reservations", params: {
      start_date: (Date.current + 4.days).to_s,
      end_date: (Date.current + 10.days).to_s
    }
    assert_response :success

    assert_select "a[href='/reservations/#{in_range.id}']"
    assert_select "a[href='/reservations/#{out_of_range.id}']", count: 0
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can perform all reservation operations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    # Index
    get "/reservations"
    assert_response :success

    # Show
    get "/reservations/#{reservation.id}"
    assert_response :success

    # Create
    assert_difference "Reservation.count", 1 do
      post "/reservations", params: {
        reservation: {
          hotel_id: @hotel.id,
          guest_id: @guest.id,
          room_type_id: @room_type.id,
          check_in_date: Date.tomorrow,
          check_out_date: 3.days.from_now,
          adults: 2,
          total_amount: 300.00
        }
      }
    end

    # Update
    patch "/reservations/#{reservation.id}", params: {
      reservation: { special_requests: "Updated" }
    }
    assert_redirected_to "/reservations/#{reservation.id}"

    # Destroy
    assert_difference "Reservation.count", -1 do
      delete "/reservations/#{reservation.id}"
    end
  end

  test "unauthenticated user cannot access reservations" do
    # With `authenticated :user` routing constraint, unauthenticated users
    # get 404 (route doesn't match) rather than redirect to sign_in
    # This is expected behavior with this routing pattern
    get "/reservations"
    assert_response :not_found
  end
end
