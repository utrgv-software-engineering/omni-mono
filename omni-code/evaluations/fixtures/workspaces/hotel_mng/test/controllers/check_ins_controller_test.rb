# frozen_string_literal: true

require "test_helper"

class CheckInsControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @guest = create(:guest, account: @account)
    @room = create(:room, hotel: @hotel, account: @account, room_type: @room_type, status: "available")
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping cannot access check_in new" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    get "/reservations/#{reservation.id}/check_in/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "housekeeping cannot create check_in" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    post "/reservations/#{reservation.id}/check_in", params: { room_id: @room.id }
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])

    reservation.reload
    assert_equal "confirmed", reservation.status
  end

  test "front_desk can access check_in new" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    get "/reservations/#{reservation.id}/check_in/new"
    assert_response :success
  end

  test "front_desk can create check_in" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    post "/reservations/#{reservation.id}/check_in", params: { room_id: @room.id }
    assert_redirected_to "/reservations/#{reservation.id}"

    reservation.reload
    assert_equal "checked_in", reservation.status
  end

  test "hotel_manager can create check_in" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    post "/reservations/#{reservation.id}/check_in", params: { room_id: @room.id }
    assert_redirected_to "/reservations/#{reservation.id}"

    reservation.reload
    assert_equal "checked_in", reservation.status
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot check in reservation from another account" do
    other_account = create(:account)
    other_reservation = nil

    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
      other_guest = create(:guest, account: other_account)
      other_reservation = create(:reservation, account: other_account, hotel: other_hotel, guest: other_guest, room_type: other_room_type, status: "confirmed")
    end

    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/reservations/#{other_reservation.id}/check_in/new"
    assert_response :not_found
  end

  # ===========================================
  # Business Logic Tests (per Testing Guide 4.1)
  # ===========================================

  test "new shows available rooms for room type" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    # Create another available room of same type
    room2 = create(:room, hotel: @hotel, account: @account, room_type: @room_type, status: "available")

    # Create occupied room of same type (should not show)
    occupied_room = create(:room, hotel: @hotel, account: @account, room_type: @room_type, status: "occupied")

    # Create available room of different type (should not show)
    other_room_type = create(:room_type, hotel: @hotel, account: @account)
    other_type_room = create(:room, hotel: @hotel, account: @account, room_type: other_room_type, status: "available")

    get "/reservations/#{reservation.id}/check_in/new"
    assert_response :success

    # Should show the two available rooms of the correct type in the select dropdown
    assert_select "select[name='room_id'] option[value='#{@room.id}']"
    assert_select "select[name='room_id'] option[value='#{room2.id}']"

    # Should NOT show occupied room or room of different type
    assert_select "select[name='room_id'] option[value='#{occupied_room.id}']", count: 0
    assert_select "select[name='room_id'] option[value='#{other_type_room.id}']", count: 0
  end

  test "create assigns room to reservation" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    post "/reservations/#{reservation.id}/check_in", params: { room_id: @room.id }

    reservation.reload
    assert_equal @room, reservation.room
  end

  test "create updates reservation status to checked_in" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    post "/reservations/#{reservation.id}/check_in", params: { room_id: @room.id }

    reservation.reload
    assert_equal "checked_in", reservation.status
  end

  test "create updates room status to occupied" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    post "/reservations/#{reservation.id}/check_in", params: { room_id: @room.id }

    @room.reload
    assert_equal "occupied", @room.status
  end

  test "create sets actual_check_in timestamp" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    freeze_time do
      post "/reservations/#{reservation.id}/check_in", params: { room_id: @room.id }

      reservation.reload
      assert_not_nil reservation.actual_check_in
      assert_in_delta Time.current, reservation.actual_check_in, 1.second
    end
  end

  test "create shows success notice" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    post "/reservations/#{reservation.id}/check_in", params: { room_id: @room.id }

    assert_redirected_to "/reservations/#{reservation.id}"
    assert_match(/checked in/i, flash[:notice])
  end

  # ===========================================
  # Edge Cases and Validation
  # ===========================================

  test "create without room_id still completes check_in" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    # No room_id - allows check-in without room assignment
    post "/reservations/#{reservation.id}/check_in"

    reservation.reload
    assert_equal "checked_in", reservation.status
    assert_nil reservation.room
  end

  test "create with invalid room_id returns not found" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    # Room.find with invalid ID should return 404
    post "/reservations/#{reservation.id}/check_in", params: { room_id: 999999 }
    assert_response :not_found
  end

  test "already checked_in reservation can be checked in again" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: @room)

    # Controller doesn't prevent re-check-in (no status validation)
    new_room = create(:room, hotel: @hotel, account: @account, room_type: @room_type, status: "available")

    post "/reservations/#{reservation.id}/check_in", params: { room_id: new_room.id }

    # Should still succeed (controller doesn't validate current status)
    reservation.reload
    assert_equal "checked_in", reservation.status
    assert_equal new_room, reservation.room
  end

  # ===========================================
  # State Transition Tests
  # ===========================================

  test "state transitions: reservation confirmed to checked_in, room available to occupied" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    assert_equal "confirmed", reservation.status
    assert_equal "available", @room.status

    post "/reservations/#{reservation.id}/check_in", params: { room_id: @room.id }

    reservation.reload
    @room.reload

    assert_equal "checked_in", reservation.status
    assert_equal "occupied", @room.status
    assert_not_nil reservation.actual_check_in
    assert_equal @room, reservation.room
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can check in guest" do
    sign_in_as(:admin, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    post "/reservations/#{reservation.id}/check_in", params: { room_id: @room.id }
    assert_redirected_to "/reservations/#{reservation.id}"

    reservation.reload
    assert_equal "checked_in", reservation.status
  end

  test "owner can check in guest" do
    sign_in_as(:owner, account: @account, hotel: @hotel)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    post "/reservations/#{reservation.id}/check_in", params: { room_id: @room.id }
    assert_redirected_to "/reservations/#{reservation.id}"

    reservation.reload
    assert_equal "checked_in", reservation.status
  end

  test "unauthenticated user cannot access check_in" do
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, status: "confirmed")

    # With `authenticated :user` routing constraint, returns 404
    get "/reservations/#{reservation.id}/check_in/new"
    assert_response :not_found
  end
end
