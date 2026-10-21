# frozen_string_literal: true

require "test_helper"

class CheckOutsControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @guest = create(:guest, account: @account)
    @room = create(:room, hotel: @hotel, account: @account, room_type: @room_type, status: "occupied")
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping cannot access check_out new" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    get "/reservations/#{reservation.id}/check_out/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "housekeeping cannot create check_out" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    post "/reservations/#{reservation.id}/check_out"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])

    reservation.reload
    assert_equal "checked_in", reservation.status
  end

  test "front_desk can access check_out new" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    get "/reservations/#{reservation.id}/check_out/new"
    assert_response :success
  end

  test "front_desk can create check_out" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    post "/reservations/#{reservation.id}/check_out"
    assert_redirected_to "/reservations/#{reservation.id}"

    reservation.reload
    assert_equal "checked_out", reservation.status
  end

  test "hotel_manager can create check_out" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    post "/reservations/#{reservation.id}/check_out"
    assert_redirected_to "/reservations/#{reservation.id}"

    reservation.reload
    assert_equal "checked_out", reservation.status
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot check out reservation from another account" do
    other_account = create(:account)
    other_reservation = nil

    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
      other_guest = create(:guest, account: other_account)
      other_room = create(:room, hotel: other_hotel, account: other_account, room_type: other_room_type, status: "occupied")
      other_reservation = create(:reservation, :checked_in, account: other_account, hotel: other_hotel, guest: other_guest, room_type: other_room_type, room: other_room)
    end

    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/reservations/#{other_reservation.id}/check_out/new"
    assert_response :not_found
  end

  # ===========================================
  # Business Logic Tests (per Testing Guide 4.1)
  # ===========================================

  test "new displays balance due" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room, total_amount: 500.00, paid_amount: 200.00)

    get "/reservations/#{reservation.id}/check_out/new"
    assert_response :success

    # Should display balance due ($500 - $200 = $300)
    assert_select "main", /300/
  end

  test "create updates reservation status to checked_out" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    post "/reservations/#{reservation.id}/check_out"

    reservation.reload
    assert_equal "checked_out", reservation.status
  end

  test "create sets actual_check_out timestamp" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    freeze_time do
      post "/reservations/#{reservation.id}/check_out"

      reservation.reload
      assert_not_nil reservation.actual_check_out
      assert_in_delta Time.current, reservation.actual_check_out, 1.second
    end
  end

  test "create updates room status to cleaning" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    post "/reservations/#{reservation.id}/check_out"

    @room.reload
    assert_equal "cleaning", @room.status
  end

  test "create shows success notice" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    post "/reservations/#{reservation.id}/check_out"

    assert_redirected_to "/reservations/#{reservation.id}"
    assert_match(/checked out/i, flash[:notice])
  end

  # ===========================================
  # Payment Processing Tests
  # ===========================================

  test "create with payment_amount adds to paid_amount" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room, total_amount: 500.00, paid_amount: 200.00)

    post "/reservations/#{reservation.id}/check_out", params: { payment_amount: "300.00" }

    reservation.reload
    assert_equal 500.00, reservation.paid_amount
  end

  test "create with full payment sets payment_status to paid" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room, total_amount: 500.00, paid_amount: 200.00)

    post "/reservations/#{reservation.id}/check_out", params: { payment_amount: "300.00" }

    reservation.reload
    assert_equal "paid", reservation.payment_status
  end

  test "create with partial payment sets payment_status to partial" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room, total_amount: 500.00, paid_amount: 0.00)

    post "/reservations/#{reservation.id}/check_out", params: { payment_amount: "200.00" }

    reservation.reload
    assert_equal "partial", reservation.payment_status
    assert_equal 200.00, reservation.paid_amount
  end

  test "create without payment_amount does not change paid_amount" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room, total_amount: 500.00, paid_amount: 200.00)

    post "/reservations/#{reservation.id}/check_out"

    reservation.reload
    assert_equal 200.00, reservation.paid_amount
    # Payment status should remain unchanged (pending from factory default)
    assert_equal "pending", reservation.payment_status
  end

  # ===========================================
  # State Transition Tests
  # ===========================================

  test "state transitions: reservation checked_in to checked_out, room occupied to cleaning" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    assert_equal "checked_in", reservation.status
    assert_equal "occupied", @room.status

    post "/reservations/#{reservation.id}/check_out"

    reservation.reload
    @room.reload

    assert_equal "checked_out", reservation.status
    assert_equal "cleaning", @room.status
    assert_not_nil reservation.actual_check_out
  end

  # ===========================================
  # Edge Cases
  # ===========================================

  test "create without assigned room still completes check_out" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    # Reservation without a room assigned
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "checked_in", actual_check_in: Time.current)

    post "/reservations/#{reservation.id}/check_out"

    reservation.reload
    assert_equal "checked_out", reservation.status
    assert_not_nil reservation.actual_check_out
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can check out guest" do
    sign_in_as(:admin, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    post "/reservations/#{reservation.id}/check_out"
    assert_redirected_to "/reservations/#{reservation.id}"

    reservation.reload
    assert_equal "checked_out", reservation.status
  end

  test "owner can check out guest" do
    sign_in_as(:owner, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    post "/reservations/#{reservation.id}/check_out"
    assert_redirected_to "/reservations/#{reservation.id}"

    reservation.reload
    assert_equal "checked_out", reservation.status
  end

  test "unauthenticated user cannot access check_out" do
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    # With `authenticated :user` routing constraint, returns 404
    get "/reservations/#{reservation.id}/check_out/new"
    assert_response :not_found
  end
end
