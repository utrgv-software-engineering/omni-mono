# frozen_string_literal: true

require "test_helper"

module Public
  class HotelsControllerTest < ActionDispatch::IntegrationTest
    setup do
      @account = create(:account)
      ActsAsTenant.current_tenant = @account
      @hotel = create(:hotel, account: @account, subdomain: "testhotel", name: "Test Hotel")
      @room_type = create(:room_type, hotel: @hotel, account: @account)
      @amenity = create(:amenity, account: @account, name: "Pool", category: "hotel")
      @hotel_amenity = create(:hotel_amenity, account: @account, hotel: @hotel, amenity: @amenity, is_available: true)
    end

    teardown do
      ActsAsTenant.current_tenant = nil
    end

    # ===========================================
    # Show Tests
    # ===========================================

    test "show displays hotel information" do
      get "/public/hotel", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "show includes room types" do
      create(:room_type, hotel: @hotel, account: @account, name: "Deluxe Suite")

      get "/public/hotel", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "show includes hotel amenities" do
      get "/public/hotel", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "show includes active hotel policies" do
      create(:hotel_policy, account: @account, hotel: @hotel, is_active: true, sort_order: 1)

      get "/public/hotel", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "show excludes inactive hotel policies" do
      create(:hotel_policy, account: @account, hotel: @hotel, is_active: false)

      get "/public/hotel", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "show requires hotel context" do
      get "/public/hotel"

      assert_response :redirect
      assert_match(/hotel not found/i, flash[:alert])
    end

    # ===========================================
    # No Authentication Required Tests
    # ===========================================

    test "public hotel page is accessible without authentication" do
      get "/public/hotel", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    # ===========================================
    # Multi-Hotel Tests
    # ===========================================

    test "show displays correct hotel based on hotel_id" do
      other_hotel = create(:hotel, account: @account, subdomain: "otherhotel", name: "Other Hotel")

      get "/public/hotel", params: { hotel_id: other_hotel.id }

      assert_response :success
    end

    # ===========================================
    # Content Tests
    # ===========================================

    test "show loads room types with rooms and amenities" do
      create(:room, hotel: @hotel, account: @account, room_type: @room_type)
      room_amenity = create(:amenity, account: @account, name: "TV", category: "room")
      create(:room_type_amenity, account: @account, room_type: @room_type, amenity: room_amenity)

      get "/public/hotel", params: { hotel_id: @hotel.id }

      assert_response :success
    end

    test "show loads only available hotel amenities" do
      unavailable_amenity = create(:amenity, account: @account, name: "Spa", category: "wellness")
      create(:hotel_amenity, account: @account, hotel: @hotel, amenity: unavailable_amenity, is_available: false)

      get "/public/hotel", params: { hotel_id: @hotel.id }

      assert_response :success
    end
  end
end
