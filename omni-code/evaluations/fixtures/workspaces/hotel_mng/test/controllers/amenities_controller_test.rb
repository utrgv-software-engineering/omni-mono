# frozen_string_literal: true

require "test_helper"

class AmenitiesControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @amenity = create(:amenity, account: @account, name: "Test Amenity", category: "hotel")
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping cannot access amenities index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/amenities"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can access amenities index" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/amenities"
    assert_response :success
  end

  test "front_desk can view amenity details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/amenities/#{@amenity.id}"
    assert_response :success
  end

  test "front_desk cannot create amenity" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/amenities/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot update amenity" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/amenities/#{@amenity.id}", params: {
      amenity: { name: "Updated Name" }
    }

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot destroy amenity" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "Amenity.count" do
      delete "/amenities/#{@amenity.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager can create amenity" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "Amenity.count", 1 do
      post "/amenities", params: {
        amenity: {
          name: "New Amenity",
          category: "room",
          icon: "star"
        }
      }
    end

    amenity = Amenity.last
    assert_redirected_to "/amenities/#{amenity.id}"
  end

  test "hotel_manager can update amenity" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/amenities/#{@amenity.id}", params: {
      amenity: { name: "Updated Amenity" }
    }

    assert_redirected_to "/amenities/#{@amenity.id}"
    @amenity.reload
    assert_equal "Updated Amenity", @amenity.name
  end

  test "hotel_manager can destroy amenity" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "Amenity.count", -1 do
      delete "/amenities/#{@amenity.id}"
    end

    assert_redirected_to "/amenities"
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns amenities list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/amenities"
    assert_response :success
  end

  test "index filters by category" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    room_amenity = create(:amenity, account: @account, name: "Room Amenity", category: "room")

    get "/amenities", params: { category: "room" }
    assert_response :success
  end

  test "show displays amenity details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/amenities/#{@amenity.id}"
    assert_response :success
  end

  test "new displays amenity form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/amenities/new"
    assert_response :success
    assert_select "form[action='/amenities']"
  end

  test "create amenity with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "Amenity.count", 1 do
      post "/amenities", params: {
        amenity: {
          name: "Spa Access",
          category: "wellness",
          icon: "spa",
          description: "Access to spa facilities",
          is_countable: false
        }
      }
    end

    amenity = Amenity.last
    assert_redirected_to "/amenities/#{amenity.id}"
    assert_equal "Spa Access", amenity.name
    assert_equal "wellness", amenity.category
  end

  test "create amenity with invalid params fails" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_no_difference "Amenity.count" do
      post "/amenities", params: {
        amenity: {
          name: "", # Name is required
          category: "hotel"
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "edit displays amenity form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/amenities/#{@amenity.id}/edit"
    assert_response :success
    assert_select "form[action='/amenities/#{@amenity.id}']"
  end

  test "update amenity with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/amenities/#{@amenity.id}", params: {
      amenity: {
        description: "Updated description",
        is_countable: true
      }
    }

    assert_redirected_to "/amenities/#{@amenity.id}"
    @amenity.reload
    assert_equal "Updated description", @amenity.description
    assert @amenity.is_countable
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access amenity from another account" do
    other_account = create(:account)
    other_amenity = nil

    ActsAsTenant.with_tenant(other_account) do
      other_amenity = create(:amenity, account: other_account)
    end

    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/amenities/#{other_amenity.id}"
    assert_response :not_found
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can perform all amenity operations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Index
    get "/amenities"
    assert_response :success

    # Show
    get "/amenities/#{@amenity.id}"
    assert_response :success

    # Create
    assert_difference "Amenity.count", 1 do
      post "/amenities", params: {
        amenity: {
          name: "Admin Amenity",
          category: "hotel"
        }
      }
    end

    # Update
    patch "/amenities/#{@amenity.id}", params: {
      amenity: { description: "Admin updated" }
    }
    assert_redirected_to "/amenities/#{@amenity.id}"

    # Destroy
    assert_difference "Amenity.count", -1 do
      delete "/amenities/#{@amenity.id}"
    end
  end

  test "unauthenticated user cannot access amenities" do
    get "/amenities"
    assert_response :not_found
  end
end
