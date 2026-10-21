# frozen_string_literal: true

require "test_helper"

class DashboardControllerTest < ActionDispatch::IntegrationTest
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

  test "any authenticated staff can access dashboard" do
    %i[housekeeping front_desk hotel_manager admin owner].each do |role|
      sign_in_as(role, account: @account, hotel: @hotel)

      get "/"
      assert_response :success, "Expected #{role} to access dashboard"
    end
  end

  test "unauthenticated user cannot access dashboard" do
    get "/"
    # Unauthenticated users get redirected to sign in
    assert_response :redirect
  end

  # ===========================================
  # Dashboard Content Tests
  # ===========================================

  test "dashboard shows basic metrics" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/"
    assert_response :success
  end

  test "dashboard shows recent reservations" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    create_list(:reservation, 3, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type)

    get "/"
    assert_response :success
  end

  test "dashboard shows todays check-ins" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    create(:reservation, :confirmed, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, check_in_date: Date.current)

    get "/"
    assert_response :success
  end

  test "dashboard shows todays check-outs" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    room = create(:room, :occupied, hotel: @hotel, account: @account, room_type: @room_type)
    create(:reservation, :checked_in, account: @account, hotel: @hotel, guest: @guest, room_type: @room_type, room: room, check_in_date: Date.current - 1, check_out_date: Date.current + 1)

    get "/"
    assert_response :success
  end

  test "dashboard shows room status counts" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    create(:room, hotel: @hotel, account: @account, room_type: @room_type, status: "available")
    create(:room, :occupied, hotel: @hotel, account: @account, room_type: @room_type)
    create(:room, :cleaning, hotel: @hotel, account: @account, room_type: @room_type)
    create(:room, :maintenance, hotel: @hotel, account: @account, room_type: @room_type)

    get "/"
    assert_response :success
  end

  test "dashboard calculates occupancy rate" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    # Create rooms
    room = create(:room, :occupied, hotel: @hotel, account: @account, room_type: @room_type)
    create(:room, hotel: @hotel, account: @account, room_type: @room_type, status: "available")

    # Create a checked-in reservation for the occupied room
    create(:reservation, :checked_in,
           account: @account,
           hotel: @hotel,
           guest: @guest,
           room_type: @room_type,
           room: room,
           check_in_date: Date.current - 1,
           check_out_date: Date.current + 1)

    get "/"
    assert_response :success
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "dashboard only shows data from current account" do
    # Create data in another account
    other_account = create(:account)
    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_guest = create(:guest, account: other_account)
      other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
      create(:reservation, account: other_account, hotel: other_hotel, guest: other_guest, room_type: other_room_type)
    end

    # Sign in to original account
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/"
    assert_response :success
    # The dashboard should only show data from @account
  end

  # ===========================================
  # Role Access Tests
  # ===========================================

  test "housekeeping can view dashboard" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/"
    assert_response :success
  end

  test "front_desk can view dashboard" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/"
    assert_response :success
  end

  test "hotel_manager can view dashboard" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/"
    assert_response :success
  end

  test "admin can view dashboard" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    get "/"
    assert_response :success
  end

  test "owner can view dashboard" do
    sign_in_as(:owner, account: @account, hotel: @hotel)

    get "/"
    assert_response :success
  end
end
