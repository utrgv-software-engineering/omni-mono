# frozen_string_literal: true

require "test_helper"

class RoomTypesControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account, name: "Standard Room", code: "STD")
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping cannot access room_types index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/room_types"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can access room_types index" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/room_types"
    assert_response :success
  end

  test "front_desk can view room_type details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/room_types/#{@room_type.id}"
    assert_response :success
  end

  test "front_desk cannot create room_type" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/room_types/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot update room_type" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/room_types/#{@room_type.id}", params: {
      room_type: { name: "Updated Name" }
    }

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot destroy room_type" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "RoomType.count" do
      delete "/hotels/#{@hotel.id}/room_types/#{@room_type.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager can create room_type" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "RoomType.count", 1 do
      post "/hotels/#{@hotel.id}/room_types", params: {
        room_type: {
          name: "Deluxe Room",
          code: "DLX",
          base_price: 250.00,
          max_occupancy: 4,
          standard_occupancy: 2
        }
      }
    end

    room_type = RoomType.last
    assert_redirected_to "/hotels/#{@hotel.id}/room_types/#{room_type.id}"
  end

  test "hotel_manager can update room_type" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/room_types/#{@room_type.id}", params: {
      room_type: { name: "Updated Name", base_price: 200.00 }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/room_types/#{@room_type.id}"
    @room_type.reload
    assert_equal "Updated Name", @room_type.name
    assert_equal 200.00, @room_type.base_price
  end

  test "hotel_manager can destroy room_type" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "RoomType.count", -1 do
      delete "/hotels/#{@hotel.id}/room_types/#{@room_type.id}"
    end

    assert_redirected_to "/hotels/#{@hotel.id}/room_types"
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access room_type from another account" do
    other_account = create(:account)
    other_hotel = nil
    other_room_type = nil

    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
    end

    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/hotels/#{other_hotel.id}/room_types/#{other_room_type.id}"
    assert_response :not_found
  end

  test "index only shows room_types from current hotel" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Create another room type in same hotel
    room_type2 = create(:room_type, hotel: @hotel, account: @account, name: "Suite", code: "STE")

    # Create room type in different hotel (same account)
    other_hotel = create(:hotel, account: @account)
    other_room_type = create(:room_type, hotel: other_hotel, account: @account, name: "Other", code: "OTH")

    get "/hotels/#{@hotel.id}/room_types"
    assert_response :success

    # Should show our hotel's room types
    assert_select "a[href='/hotels/#{@hotel.id}/room_types/#{@room_type.id}']"
    assert_select "a[href='/hotels/#{@hotel.id}/room_types/#{room_type2.id}']"
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns room_types list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/room_types"
    assert_response :success
    assert_select "a[href='/hotels/#{@hotel.id}/room_types/#{@room_type.id}']"
  end

  test "show displays room_type details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/room_types/#{@room_type.id}"
    assert_response :success
    assert_select "main", /#{@room_type.name}/
  end

  test "new displays room_type form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/room_types/new"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/room_types']"
  end

  test "create room_type with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "RoomType.count", 1 do
      post "/hotels/#{@hotel.id}/room_types", params: {
        room_type: {
          name: "Executive Suite",
          code: "EXE",
          description: "Luxurious executive suite",
          base_price: 500.00,
          max_occupancy: 4,
          standard_occupancy: 2,
          extra_person_charge: 50.00,
          size_sqm: 75.0
        }
      }
    end

    room_type = RoomType.last
    assert_redirected_to "/hotels/#{@hotel.id}/room_types/#{room_type.id}"
    assert_equal "Executive Suite", room_type.name
    assert_equal "EXE", room_type.code
    assert_equal 500.00, room_type.base_price
    assert_equal @hotel, room_type.hotel
    assert_equal @account, room_type.account
  end

  test "create room_type with invalid params fails" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_no_difference "RoomType.count" do
      post "/hotels/#{@hotel.id}/room_types", params: {
        room_type: {
          name: "", # Required
          code: "INV",
          base_price: 100.00,
          max_occupancy: 2,
          standard_occupancy: 1
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "create validates standard_occupancy <= max_occupancy" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_no_difference "RoomType.count" do
      post "/hotels/#{@hotel.id}/room_types", params: {
        room_type: {
          name: "Invalid Room",
          code: "INV",
          base_price: 100.00,
          max_occupancy: 2,
          standard_occupancy: 4 # Invalid: greater than max
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "edit displays room_type form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/room_types/#{@room_type.id}/edit"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/room_types/#{@room_type.id}']"
  end

  test "update room_type with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/room_types/#{@room_type.id}", params: {
      room_type: {
        description: "Updated description",
        extra_person_charge: 30.00
      }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/room_types/#{@room_type.id}"
    @room_type.reload
    assert_equal "Updated description", @room_type.description
    assert_equal 30.00, @room_type.extra_person_charge
  end

  test "update room_type with invalid params fails" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/room_types/#{@room_type.id}", params: {
      room_type: { name: "" } # Name is required
    }

    assert_response :unprocessable_content
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can perform all room_type operations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Index
    get "/hotels/#{@hotel.id}/room_types"
    assert_response :success

    # Show
    get "/hotels/#{@hotel.id}/room_types/#{@room_type.id}"
    assert_response :success

    # Create
    assert_difference "RoomType.count", 1 do
      post "/hotels/#{@hotel.id}/room_types", params: {
        room_type: {
          name: "Admin Room",
          code: "ADM",
          base_price: 100.00,
          max_occupancy: 2,
          standard_occupancy: 1
        }
      }
    end

    # Update
    patch "/hotels/#{@hotel.id}/room_types/#{@room_type.id}", params: {
      room_type: { description: "Admin updated" }
    }
    assert_redirected_to "/hotels/#{@hotel.id}/room_types/#{@room_type.id}"

    # Destroy
    assert_difference "RoomType.count", -1 do
      delete "/hotels/#{@hotel.id}/room_types/#{@room_type.id}"
    end
  end

  test "owner can perform all room_type operations" do
    sign_in_as(:owner, account: @account, hotel: @hotel)

    # Index
    get "/hotels/#{@hotel.id}/room_types"
    assert_response :success

    # Create
    assert_difference "RoomType.count", 1 do
      post "/hotels/#{@hotel.id}/room_types", params: {
        room_type: {
          name: "Owner Room",
          code: "OWN",
          base_price: 100.00,
          max_occupancy: 2,
          standard_occupancy: 1
        }
      }
    end

    # Destroy
    assert_difference "RoomType.count", -1 do
      delete "/hotels/#{@hotel.id}/room_types/#{@room_type.id}"
    end
  end

  test "unauthenticated user cannot access room_types" do
    # With `authenticated :user` routing constraint, returns 404
    get "/hotels/#{@hotel.id}/room_types"
    assert_response :not_found
  end
end
