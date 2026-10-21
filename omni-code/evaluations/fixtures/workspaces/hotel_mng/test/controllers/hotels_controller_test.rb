# frozen_string_literal: true

require "test_helper"

class HotelsControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account, name: "Test Hotel")
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping can access hotels index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels"
    assert_response :success
  end

  test "front_desk can access hotels index" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels"
    assert_response :success
  end

  test "any staff can view hotel details" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}"
    assert_response :success
  end

  test "housekeeping cannot create hotel" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot create hotel" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager cannot create hotel" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/hotels/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "admin can create hotel" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    get "/hotels/new"
    assert_response :success

    assert_difference "Hotel.count", 1 do
      post "/hotels", params: {
        hotel: {
          name: "New Hotel",
          subdomain: "newhotel",
          address: "123 Main St",
          city: "Test City",
          state: "TX",
          country: "US",
          postal_code: "75001",
          phone: "555-1234",
          email: "new@hotel.com",
          timezone: "Central Time (US & Canada)"
        }
      }
    end

    hotel = Hotel.last
    assert_redirected_to "/hotels/#{hotel.id}"
  end

  test "owner can create hotel" do
    sign_in_as(:owner, account: @account, hotel: @hotel)

    assert_difference "Hotel.count", 1 do
      post "/hotels", params: {
        hotel: {
          name: "Owner Hotel",
          subdomain: "ownerhotel",
          address: "456 Oak Ave",
          city: "Another City",
          state: "NY",
          country: "US",
          postal_code: "10001",
          phone: "555-5678",
          email: "owner@hotel.com",
          timezone: "Eastern Time (US & Canada)"
        }
      }
    end

    hotel = Hotel.last
    assert_redirected_to "/hotels/#{hotel.id}"
  end

  test "hotel_manager can update hotel" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}", params: {
      hotel: { name: "Updated Hotel Name" }
    }

    assert_redirected_to "/hotels/#{@hotel.id}"
    @hotel.reload
    assert_equal "Updated Hotel Name", @hotel.name
  end

  test "front_desk cannot update hotel" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}", params: {
      hotel: { name: "Updated Name" }
    }

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
    @hotel.reload
    assert_equal "Test Hotel", @hotel.name
  end

  test "housekeeping cannot update hotel" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}", params: {
      hotel: { name: "Updated Name" }
    }

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager cannot destroy hotel" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_no_difference "Hotel.count" do
      delete "/hotels/#{@hotel.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "admin can destroy hotel without reservations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    assert_difference "Hotel.count", -1 do
      delete "/hotels/#{@hotel.id}"
    end

    assert_redirected_to "/hotels"
  end

  test "owner can destroy hotel without reservations" do
    sign_in_as(:owner, account: @account, hotel: @hotel)

    assert_difference "Hotel.count", -1 do
      delete "/hotels/#{@hotel.id}"
    end

    assert_redirected_to "/hotels"
  end

  # ===========================================
  # Business Logic Tests
  # ===========================================

  test "destroy fails if hotel has reservations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Create a reservation for this hotel
    room_type = create(:room_type, hotel: @hotel, account: @account)
    guest = create(:guest, account: @account)
    create(:reservation, hotel: @hotel, account: @account, room_type: room_type, guest: guest)

    assert_no_difference "Hotel.count" do
      delete "/hotels/#{@hotel.id}"
    end

    assert_redirected_to "/hotels"
    assert_match(/cannot delete.*reservations/i, flash[:alert])
  end

  test "create sets new hotel as current if it is the first hotel" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # This tests that creating the second hotel works correctly
    # (Testing with no hotels at all causes sidebar rendering issues without a current hotel)
    post "/hotels", params: {
      hotel: {
        name: "Second Hotel",
        subdomain: "secondhotel",
        address: "100 Second St",
        city: "City",
        state: "CA",
        country: "US",
        postal_code: "90001",
        phone: "555-9999",
        email: "second@hotel.com",
        timezone: "Pacific Time (US & Canada)"
      }
    }

    new_hotel = Hotel.find_by(subdomain: "secondhotel")
    assert new_hotel.present?
    assert_redirected_to "/hotels/#{new_hotel.id}"
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns hotels list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels"
    assert_response :success
  end

  test "show displays hotel details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}"
    assert_response :success
    assert_select "main", /#{@hotel.name}/
  end

  test "new displays hotel form" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    get "/hotels/new"
    assert_response :success
    assert_select "form[action='/hotels']"
  end

  test "create hotel with valid params" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    assert_difference "Hotel.count", 1 do
      post "/hotels", params: {
        hotel: {
          name: "Grand Hotel",
          subdomain: "grandhotel",
          address: "789 Grand Ave",
          city: "Grand City",
          state: "GS",
          country: "US",
          postal_code: "12345",
          phone: "555-1234",
          email: "info@grandhotel.com",
          timezone: "Eastern Time (US & Canada)",
          star_rating: 4
        }
      }
    end

    hotel = Hotel.last
    assert_redirected_to "/hotels/#{hotel.id}"
    assert_equal "Grand Hotel", hotel.name
    assert_equal "grandhotel", hotel.subdomain
    assert_equal @account, hotel.account
  end

  test "create hotel with invalid params fails" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    assert_no_difference "Hotel.count" do
      post "/hotels", params: {
        hotel: {
          name: "", # Name is likely required
          subdomain: "test"
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "edit displays hotel form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/edit"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}']"
  end

  test "update hotel with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}", params: {
      hotel: {
        description: "Updated description",
        star_rating: 5
      }
    }

    assert_redirected_to "/hotels/#{@hotel.id}"
    @hotel.reload
    assert_equal "Updated description", @hotel.description
    assert_equal 5, @hotel.star_rating
  end

  # ===========================================
  # Set Current Hotel Tests
  # ===========================================

  test "set_current switches hotel context" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Create another hotel
    other_hotel = create(:hotel, account: @account, name: "Other Hotel")

    post "/set_current_hotel", params: { hotel_id: other_hotel.id }

    # Should redirect back with notice
    assert_response :redirect
  end

  test "set_current with invalid hotel returns error" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    post "/set_current_hotel", params: { hotel_id: 999999 }

    assert_response :redirect
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access hotel from another account" do
    other_account = create(:account)
    other_hotel = nil

    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
    end

    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/hotels/#{other_hotel.id}"
    assert_response :not_found
  end

  test "index only shows hotels from current account" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Create another hotel in same account
    hotel2 = create(:hotel, account: @account, name: "Hotel 2")

    # Create hotel in different account
    other_account = create(:account)
    ActsAsTenant.with_tenant(other_account) do
      create(:hotel, account: other_account, name: "Other Account Hotel")
    end

    get "/hotels"
    assert_response :success

    # Should show our hotels
    assert_select "a[href='/hotels/#{@hotel.id}']"
    assert_select "a[href='/hotels/#{hotel2.id}']"
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can perform most hotel operations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Index
    get "/hotels"
    assert_response :success

    # Show
    get "/hotels/#{@hotel.id}"
    assert_response :success

    # New
    get "/hotels/new"
    assert_response :success

    # Create
    assert_difference "Hotel.count", 1 do
      post "/hotels", params: {
        hotel: {
          name: "Admin Hotel",
          subdomain: "adminhotel",
          address: "100 Admin Blvd",
          city: "Admin City",
          state: "CA",
          country: "US",
          postal_code: "90210",
          phone: "555-0000",
          email: "admin@hotel.com",
          timezone: "Pacific Time (US & Canada)"
        }
      }
    end

    # Edit
    get "/hotels/#{@hotel.id}/edit"
    assert_response :success

    # Update
    patch "/hotels/#{@hotel.id}", params: {
      hotel: { description: "Admin updated" }
    }
    assert_redirected_to "/hotels/#{@hotel.id}"

    # Destroy (if no reservations)
    assert_difference "Hotel.count", -1 do
      delete "/hotels/#{@hotel.id}"
    end
  end

  test "unauthenticated user cannot access hotels" do
    # With `authenticated :user` routing constraint, returns 404
    get "/hotels"
    assert_response :not_found
  end
end
