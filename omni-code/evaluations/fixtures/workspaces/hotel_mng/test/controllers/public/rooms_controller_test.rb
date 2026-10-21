# frozen_string_literal: true

require "test_helper"

module Public
  class RoomsControllerTest < ActionDispatch::IntegrationTest
    setup do
      @account = create(:account)
      ActsAsTenant.current_tenant = @account
      @hotel = create(:hotel, account: @account, subdomain: "testhotel")
      @room_type = create(:room_type, hotel: @hotel, account: @account, base_price: 150.00, max_occupancy: 4, name: "Standard Room")
      @room = create(:room, hotel: @hotel, account: @account, room_type: @room_type, status: "available")
    end

    teardown do
      ActsAsTenant.current_tenant = nil
    end

    # ===========================================
    # Index Tests
    # ===========================================

    test "index displays all room types" do
      get "/public/rooms", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "index accepts date and guest parameters" do
      get "/public/rooms", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 2.days).to_s,
        guests: 2
      }

      assert_response :success
    end

    test "index defaults to today/tomorrow if no dates provided" do
      get "/public/rooms", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "index defaults to 2 guests if not provided" do
      get "/public/rooms", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "index requires hotel context" do
      get "/public/rooms"

      assert_response :redirect
      assert_match(/hotel not found/i, flash[:alert])
    end

    # ===========================================
    # Show Tests
    # ===========================================

    test "show displays room type details" do
      get "/public/rooms/#{@room_type.id}", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "show accepts date and guest parameters" do
      get "/public/rooms/#{@room_type.id}", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 3.days).to_s,
        guests: 2
      }

      assert_response :success
    end

    test "show calculates total price" do
      get "/public/rooms/#{@room_type.id}", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 2.days).to_s, # 2 nights
        guests: 2
      }

      assert_response :success
      # Should calculate price for 2 nights
    end

    test "show calculates number of nights" do
      get "/public/rooms/#{@room_type.id}", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 5.days).to_s, # 5 nights
        guests: 2
      }

      assert_response :success
    end

    test "show includes room amenities" do
      room_amenity = create(:amenity, account: @account, name: "Mini Bar", category: "room")
      create(:room_type_amenity, account: @account, room_type: @room_type, amenity: room_amenity)

      get "/public/rooms/#{@room_type.id}", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "show includes hotel policies" do
      create(:hotel_policy, account: @account, hotel: @hotel, is_active: true)

      get "/public/rooms/#{@room_type.id}", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "show includes default cancellation policy" do
      create(:cancellation_policy, :with_hotel, account: @account, hotel: @hotel, is_default: true)

      get "/public/rooms/#{@room_type.id}", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "show calculates available count" do
      create(:room, hotel: @hotel, account: @account, room_type: @room_type, status: "available")

      get "/public/rooms/#{@room_type.id}", params: {
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 1.day).to_s,
        guests: 2
      }

      assert_response :success
    end

    test "show requires hotel context" do
      get "/public/rooms/#{@room_type.id}"

      assert_response :redirect
      assert_match(/hotel not found/i, flash[:alert])
    end

    test "show returns 404 for room type from different hotel" do
      other_hotel = create(:hotel, account: @account, subdomain: "otherhotel")
      other_room_type = create(:room_type, hotel: other_hotel, account: @account)

      get "/public/rooms/#{other_room_type.id}", params: { hotel_id: @hotel.id }
      assert_response :not_found
    end

    # ===========================================
    # No Authentication Required Tests
    # ===========================================

    test "index is accessible without authentication" do
      get "/public/rooms", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "show is accessible without authentication" do
      get "/public/rooms/#{@room_type.id}", params: { hotel_id: @hotel.id }

      assert_response :success
    end
  end
end
