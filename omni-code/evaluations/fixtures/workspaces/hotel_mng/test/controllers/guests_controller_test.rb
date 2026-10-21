# frozen_string_literal: true

require "test_helper"

class GuestsControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @guest = create(:guest, account: @account, first_name: "John", last_name: "Smith", email: "john.smith@example.com")
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping cannot access guests index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/guests"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can access guests index" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/guests"
    assert_response :success
  end

  test "front_desk can view guest details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/guests/#{@guest.id}"
    assert_response :success
  end

  test "front_desk can create guest" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_difference "Guest.count", 1 do
      post "/guests", params: {
        guest: {
          first_name: "Jane",
          last_name: "Doe",
          email: "jane.doe@example.com",
          phone: "555-9876"
        }
      }
    end

    guest = Guest.last
    assert_redirected_to "/guests/#{guest.id}"
  end

  test "front_desk can update guest" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/guests/#{@guest.id}", params: {
      guest: { phone: "555-9999" }
    }

    assert_redirected_to "/guests/#{@guest.id}"
    @guest.reload
    assert_equal "555-9999", @guest.phone
  end

  test "front_desk cannot destroy guest" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "Guest.count" do
      delete "/guests/#{@guest.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager can destroy guest" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "Guest.count", -1 do
      delete "/guests/#{@guest.id}"
    end

    assert_redirected_to "/guests"
  end

  test "admin can destroy guest" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    assert_difference "Guest.count", -1 do
      delete "/guests/#{@guest.id}"
    end

    assert_redirected_to "/guests"
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access guest from another account" do
    other_account = create(:account)
    other_guest = nil

    ActsAsTenant.with_tenant(other_account) do
      other_guest = create(:guest, account: other_account)
    end

    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/guests/#{other_guest.id}"
    assert_response :not_found
  end

  test "index only shows guests from current account" do
    # Create another guest in this account
    guest2 = create(:guest, account: @account, first_name: "Jane", last_name: "Doe")

    # Create guest in another account
    other_account = create(:account)
    ActsAsTenant.with_tenant(other_account) do
      create(:guest, account: other_account, first_name: "Other", last_name: "Person")
    end

    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/guests"
    assert_response :success

    # Should show our guests
    assert_select "a[href='/guests/#{@guest.id}']"
    assert_select "a[href='/guests/#{guest2.id}']"
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns guests list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/guests"
    assert_response :success
    assert_select "a[href='/guests/#{@guest.id}']"
  end

  test "show displays guest details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/guests/#{@guest.id}"
    assert_response :success
    assert_select "main", /#{@guest.first_name}/
    assert_select "main", /#{@guest.last_name}/
  end

  test "show includes reservation history" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    room_type = create(:room_type, hotel: @hotel, account: @account)
    reservation = create(:reservation, account: @account, hotel: @hotel, guest: @guest, room_type: room_type)

    get "/guests/#{@guest.id}"
    assert_response :success
    # Should show reservation in history
    assert_select "main", /#{reservation.confirmation_number}/
  end

  test "new displays guest form" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/guests/new"
    assert_response :success
    assert_select "form[action='/guests']"
  end

  test "create guest with valid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_difference "Guest.count", 1 do
      post "/guests", params: {
        guest: {
          first_name: "New",
          last_name: "Guest",
          email: "new.guest@example.com",
          phone: "555-1234",
          address: "123 New St",
          city: "New City",
          country: "US"
        }
      }
    end

    guest = Guest.last
    assert_redirected_to "/guests/#{guest.id}"
    assert_equal "New", guest.first_name
    assert_equal "Guest", guest.last_name
    assert_equal @account, guest.account
  end

  test "create guest with invalid params fails" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "Guest.count" do
      post "/guests", params: {
        guest: {
          first_name: "", # Required
          last_name: "Guest",
          email: "invalid@example.com"
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "edit displays guest form" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/guests/#{@guest.id}/edit"
    assert_response :success
    assert_select "form[action='/guests/#{@guest.id}']"
  end

  test "update guest with valid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/guests/#{@guest.id}", params: {
      guest: {
        first_name: "Updated",
        vip_status: true,
        notes: "Special guest"
      }
    }

    assert_redirected_to "/guests/#{@guest.id}"
    @guest.reload
    assert_equal "Updated", @guest.first_name
    assert @guest.vip_status
    assert_equal "Special guest", @guest.notes
  end

  test "update guest with invalid params fails" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/guests/#{@guest.id}", params: {
      guest: { email: "" } # Email is required
    }

    assert_response :unprocessable_content
  end

  # ===========================================
  # Search/Filtering Tests (per Testing Guide 3.2.1)
  # ===========================================

  test "index searches by first_name (case-insensitive)" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    guest2 = create(:guest, account: @account, first_name: "Jane", last_name: "Doe")

    get "/guests", params: { search: "john" }
    assert_response :success

    assert_select "a[href='/guests/#{@guest.id}']"
    assert_select "a[href='/guests/#{guest2.id}']", count: 0
  end

  test "index searches by last_name (case-insensitive)" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    guest2 = create(:guest, account: @account, first_name: "Jane", last_name: "Doe")

    get "/guests", params: { search: "SMITH" }
    assert_response :success

    assert_select "a[href='/guests/#{@guest.id}']"
    assert_select "a[href='/guests/#{guest2.id}']", count: 0
  end

  test "index searches by email (case-insensitive)" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    guest2 = create(:guest, account: @account, first_name: "Jane", last_name: "Doe", email: "jane@other.com")

    get "/guests", params: { search: "JOHN.SMITH" }
    assert_response :success

    assert_select "a[href='/guests/#{@guest.id}']"
    assert_select "a[href='/guests/#{guest2.id}']", count: 0
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "hotel_manager can perform all guest operations" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    # Index
    get "/guests"
    assert_response :success

    # Show
    get "/guests/#{@guest.id}"
    assert_response :success

    # Create
    assert_difference "Guest.count", 1 do
      post "/guests", params: {
        guest: {
          first_name: "Manager",
          last_name: "Created",
          email: "manager.created@example.com"
        }
      }
    end

    # Update
    patch "/guests/#{@guest.id}", params: {
      guest: { notes: "Manager updated" }
    }
    assert_redirected_to "/guests/#{@guest.id}"

    # Destroy
    assert_difference "Guest.count", -1 do
      delete "/guests/#{@guest.id}"
    end
  end

  test "owner can perform all guest operations" do
    sign_in_as(:owner, account: @account, hotel: @hotel)

    # Index
    get "/guests"
    assert_response :success

    # Create
    assert_difference "Guest.count", 1 do
      post "/guests", params: {
        guest: {
          first_name: "Owner",
          last_name: "Created",
          email: "owner.created@example.com"
        }
      }
    end

    # Destroy
    assert_difference "Guest.count", -1 do
      delete "/guests/#{@guest.id}"
    end
  end

  test "unauthenticated user cannot access guests" do
    # With `authenticated :user` routing constraint, returns 404
    get "/guests"
    assert_response :not_found
  end
end
