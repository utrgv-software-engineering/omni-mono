# frozen_string_literal: true

require "test_helper"

class ServicesControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @service = create(:service, account: @account, hotel: @hotel, name: "Spa Treatment", category: "spa")
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping cannot access services index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/services"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can access services index" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/services"
    assert_response :success
  end

  test "front_desk can view service details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/services/#{@service.id}"
    assert_response :success
  end

  test "front_desk cannot create service" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/services/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot update service" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/services/#{@service.id}", params: {
      service: { name: "Updated Name" }
    }

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot destroy service" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "Service.count" do
      delete "/hotels/#{@hotel.id}/services/#{@service.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager can create service" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "Service.count", 1 do
      post "/hotels/#{@hotel.id}/services", params: {
        service: {
          name: "New Service",
          category: "dining",
          price: 100.00
        }
      }
    end

    service = Service.last
    assert_redirected_to "/hotels/#{@hotel.id}/services/#{service.id}"
  end

  test "hotel_manager can update service" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/services/#{@service.id}", params: {
      service: { name: "Updated Service", price: 200.00 }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/services/#{@service.id}"
    @service.reload
    assert_equal "Updated Service", @service.name
    assert_equal 200.00, @service.price.to_f
  end

  test "hotel_manager can destroy service" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "Service.count", -1 do
      delete "/hotels/#{@hotel.id}/services/#{@service.id}"
    end

    assert_redirected_to "/hotels/#{@hotel.id}/services"
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns services list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/services"
    assert_response :success
  end

  test "show displays service details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/services/#{@service.id}"
    assert_response :success
  end

  test "new displays service form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/services/new"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/services']"
  end

  test "create service with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "Service.count", 1 do
      post "/hotels/#{@hotel.id}/services", params: {
        service: {
          name: "Airport Shuttle",
          category: "transport",
          description: "Transfer service to/from airport",
          price: 50.00,
          pricing_unit: "per_person",
          duration_minutes: 45,
          capacity: 8,
          advance_booking_required: true,
          is_active: true
        }
      }
    end

    service = Service.last
    assert_redirected_to "/hotels/#{@hotel.id}/services/#{service.id}"
    assert_equal "Airport Shuttle", service.name
    assert_equal "transport", service.category
    assert_equal @hotel, service.hotel
    assert_equal @account, service.account
  end

  test "create service with invalid params fails" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_no_difference "Service.count" do
      post "/hotels/#{@hotel.id}/services", params: {
        service: {
          name: "", # Name is required
          category: "spa",
          price: 100.00
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "edit displays service form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/services/#{@service.id}/edit"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/services/#{@service.id}']"
  end

  test "update service with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/services/#{@service.id}", params: {
      service: {
        description: "Premium spa experience",
        is_active: false
      }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/services/#{@service.id}"
    @service.reload
    assert_equal "Premium spa experience", @service.description
    assert_not @service.is_active
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access service from another account" do
    other_account = create(:account)
    other_service = nil

    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_service = create(:service, account: other_account, hotel: other_hotel)
    end

    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/hotels/#{other_service.hotel_id}/services/#{other_service.id}"
    assert_response :not_found
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can perform all service operations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Index
    get "/hotels/#{@hotel.id}/services"
    assert_response :success

    # Show
    get "/hotels/#{@hotel.id}/services/#{@service.id}"
    assert_response :success

    # Create
    assert_difference "Service.count", 1 do
      post "/hotels/#{@hotel.id}/services", params: {
        service: {
          name: "Admin Service",
          category: "other",
          price: 75.00
        }
      }
    end

    # Update
    patch "/hotels/#{@hotel.id}/services/#{@service.id}", params: {
      service: { description: "Admin updated" }
    }
    assert_redirected_to "/hotels/#{@hotel.id}/services/#{@service.id}"

    # Destroy
    assert_difference "Service.count", -1 do
      delete "/hotels/#{@hotel.id}/services/#{@service.id}"
    end
  end

  test "unauthenticated user cannot access services" do
    get "/hotels/#{@hotel.id}/services"
    assert_response :not_found
  end
end
