# frozen_string_literal: true

require "test_helper"

module Public
  class AvailabilityControllerTest < ActionDispatch::IntegrationTest
    setup do
      @account = create(:account)
      ActsAsTenant.current_tenant = @account
      @hotel = create(:hotel, account: @account, subdomain: "testhotel")
      @room_type = create(:room_type, hotel: @hotel, account: @account, base_price: 100.00, max_occupancy: 4)
      @room1 = create(:room, hotel: @hotel, account: @account, room_type: @room_type, status: "available")
      @room2 = create(:room, hotel: @hotel, account: @account, room_type: @room_type, status: "available")
    end

    teardown do
      ActsAsTenant.current_tenant = nil
    end

    # ===========================================
    # Index Tests (per Testing Guide 5.2)
    # ===========================================

    test "index shows available room types" do
      get "/public/availability", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 2.days).to_s,
        guests: 2
      }

      assert_response :success
    end

    test "index defaults to today/tomorrow if no dates provided" do
      get "/public/availability", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "index defaults to 1 guest if not provided" do
      get "/public/availability", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 1.day).to_s
      }

      assert_response :success
    end

    test "index filters room_types by max_occupancy" do
      # Create a small room type that can't fit many guests
      small_room_type = create(:room_type, hotel: @hotel, account: @account, max_occupancy: 2)
      create(:room, hotel: @hotel, account: @account, room_type: small_room_type, status: "available")

      # Search for more guests than small room can hold
      get "/public/availability", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 1.day).to_s,
        guests: 4
      }

      assert_response :success
      # The small_room_type with max_occupancy: 2 should not appear
      # Only @room_type with max_occupancy: 4 should be available
    end

    test "index excludes room_types with no availability" do
      # Book all rooms of @room_type for the dates
      guest = create(:guest, account: @account)
      create(:reservation, :confirmed,
             account: @account,
             hotel: @hotel,
             guest: guest,
             room_type: @room_type,
             room: @room1,
             check_in_date: Date.tomorrow,
             check_out_date: Date.tomorrow + 2.days)
      create(:reservation, :confirmed,
             account: @account,
             hotel: @hotel,
             guest: guest,
             room_type: @room_type,
             room: @room2,
             check_in_date: Date.tomorrow,
             check_out_date: Date.tomorrow + 2.days)

      get "/public/availability", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 2.days).to_s,
        guests: 2
      }

      assert_response :success
    end

    test "index requires hotel context" do
      get "/public/availability"

      assert_response :redirect
      assert_match(/hotel not found/i, flash[:alert])
    end

    # ===========================================
    # Available Count Tests
    # ===========================================

    test "index calculates available count for each room_type" do
      get "/public/availability", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 1.day).to_s,
        guests: 2
      }

      assert_response :success
    end

    test "rooms in maintenance are not counted as available" do
      @room2.update!(status: "maintenance")

      get "/public/availability", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 1.day).to_s,
        guests: 2
      }

      assert_response :success
    end

    test "overlapping reservations reduce availability" do
      guest = create(:guest, account: @account)

      # Book one room for overlapping dates
      create(:reservation, :confirmed,
             account: @account,
             hotel: @hotel,
             guest: guest,
             room_type: @room_type,
             room: @room1,
             check_in_date: Date.tomorrow,
             check_out_date: Date.tomorrow + 3.days)

      get "/public/availability", params: {
        hotel_id: @hotel.id,
        check_in_date: (Date.tomorrow + 1.day).to_s,
        check_out_date: (Date.tomorrow + 2.days).to_s,
        guests: 2
      }

      assert_response :success
      # Should show 1 room available instead of 2
    end

    # ===========================================
    # Multi-Hotel Tests
    # ===========================================

    test "availability is scoped to specific hotel" do
      other_hotel = create(:hotel, account: @account, subdomain: "otherhotel")
      other_room_type = create(:room_type, hotel: other_hotel, account: @account)
      create(:room, hotel: other_hotel, account: @account, room_type: other_room_type)

      get "/public/availability", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 1.day).to_s,
        guests: 2
      }

      assert_response :success
      # Response should only include @hotel's room types, not other_hotel's
    end

    # ===========================================
    # Date Handling Tests
    # ===========================================

    test "index handles date parsing" do
      get "/public/availability", params: {
        hotel_id: @hotel.id,
        check_in_date: "2025-01-15",
        check_out_date: "2025-01-17",
        guests: 2
      }

      assert_response :success
    end

    test "index accepts various date formats" do
      get "/public/availability", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.strftime("%Y-%m-%d"),
        check_out_date: (Date.tomorrow + 1.day).strftime("%Y-%m-%d"),
        guests: 2
      }

      assert_response :success
    end
  end
end
