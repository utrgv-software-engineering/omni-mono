# frozen_string_literal: true

require "test_helper"

class HotelAmenitiesControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @amenity = create(:amenity, account: @account, name: "Pool", category: "hotel")
    @hotel_amenity = create(:hotel_amenity, account: @account, hotel: @hotel, amenity: @amenity)
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping cannot access hotel_amenities index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/hotel_amenities"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can access hotel_amenities index" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/hotel_amenities"
    assert_response :success
  end

  test "front_desk can view hotel_amenity details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}"
    assert_response :success
  end

  test "front_desk cannot create hotel_amenity" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/hotel_amenities/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot update hotel_amenity" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}", params: {
      hotel_amenity: { is_available: false }
    }

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot destroy hotel_amenity" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "HotelAmenity.count" do
      delete "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager can create hotel_amenity" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)
    new_amenity = create(:amenity, account: @account, name: "Gym", category: "hotel")

    assert_difference "HotelAmenity.count", 1 do
      post "/hotels/#{@hotel.id}/hotel_amenities", params: {
        hotel_amenity: {
          amenity_id: new_amenity.id,
          is_available: true,
          is_free: true
        }
      }
    end

    hotel_amenity = HotelAmenity.last
    assert_redirected_to "/hotels/#{@hotel.id}/hotel_amenities/#{hotel_amenity.id}"
  end

  test "hotel_manager can update hotel_amenity" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}", params: {
      hotel_amenity: { is_available: false, notes: "Under maintenance" }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}"
    @hotel_amenity.reload
    assert_not @hotel_amenity.is_available
    assert_equal "Under maintenance", @hotel_amenity.notes
  end

  test "hotel_manager can destroy hotel_amenity" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "HotelAmenity.count", -1 do
      delete "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}"
    end

    assert_redirected_to "/hotels/#{@hotel.id}/hotel_amenities"
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns hotel_amenities list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/hotel_amenities"
    assert_response :success
  end

  test "show displays hotel_amenity details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}"
    assert_response :success
  end

  test "new displays hotel_amenity form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)
    # Create an available amenity with a category that shows in the form
    create(:amenity, account: @account, name: "Available Amenity", category: "hotel")

    get "/hotels/#{@hotel.id}/hotel_amenities/new"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/hotel_amenities']"
  end

  test "create hotel_amenity with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)
    new_amenity = create(:amenity, account: @account, name: "Spa", category: "wellness")

    assert_difference "HotelAmenity.count", 1 do
      post "/hotels/#{@hotel.id}/hotel_amenities", params: {
        hotel_amenity: {
          amenity_id: new_amenity.id,
          is_available: true,
          is_free: false,
          additional_cost: 100.00,
          location: "3rd Floor",
          operating_hours: "9 AM - 9 PM"
        }
      }
    end

    hotel_amenity = HotelAmenity.last
    assert_redirected_to "/hotels/#{@hotel.id}/hotel_amenities/#{hotel_amenity.id}"
    assert_equal new_amenity, hotel_amenity.amenity
    assert_equal @hotel, hotel_amenity.hotel
    assert_not hotel_amenity.is_free
    assert_equal 100.00, hotel_amenity.additional_cost.to_f
  end

  test "edit displays hotel_amenity form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}/edit"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}']"
  end

  test "update hotel_amenity with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}", params: {
      hotel_amenity: {
        location: "Rooftop",
        operating_hours: "6 AM - 10 PM"
      }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}"
    @hotel_amenity.reload
    assert_equal "Rooftop", @hotel_amenity.location
    assert_equal "6 AM - 10 PM", @hotel_amenity.operating_hours
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access hotel_amenity from another account" do
    other_account = create(:account)
    other_hotel_amenity = nil

    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_amenity = create(:amenity, account: other_account)
      other_hotel_amenity = create(:hotel_amenity, account: other_account, hotel: other_hotel, amenity: other_amenity)
    end

    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/hotels/#{other_hotel_amenity.hotel_id}/amenities/#{other_hotel_amenity.id}"
    assert_response :not_found
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can perform all hotel_amenity operations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)
    new_amenity = create(:amenity, account: @account, name: "Restaurant", category: "hotel")

    # Index
    get "/hotels/#{@hotel.id}/hotel_amenities"
    assert_response :success

    # Show
    get "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}"
    assert_response :success

    # Create
    assert_difference "HotelAmenity.count", 1 do
      post "/hotels/#{@hotel.id}/hotel_amenities", params: {
        hotel_amenity: {
          amenity_id: new_amenity.id,
          is_available: true
        }
      }
    end

    # Update
    patch "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}", params: {
      hotel_amenity: { notes: "Admin updated" }
    }
    assert_redirected_to "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}"

    # Destroy
    assert_difference "HotelAmenity.count", -1 do
      delete "/hotels/#{@hotel.id}/hotel_amenities/#{@hotel_amenity.id}"
    end
  end

  test "unauthenticated user cannot access hotel_amenities" do
    get "/hotels/#{@hotel.id}/hotel_amenities"
    assert_response :not_found
  end
end
