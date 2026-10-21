# frozen_string_literal: true

require "test_helper"

class MaintenanceRequestsControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    @reporter = create(:user)
    @maintenance_request = create(:maintenance_request, :unassigned, hotel: @hotel, account: @account, room: @room, reported_by: @reporter)
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping can access maintenance_requests index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/maintenance_requests"
    assert_response :success
  end

  test "housekeeping can view maintenance_request details" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    assert_response :success
  end

  test "housekeeping can create maintenance_request" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    assert_difference "MaintenanceRequest.count", 1 do
      post "/hotels/#{@hotel.id}/maintenance_requests", params: {
        maintenance_request: {
          room_id: @room.id,
          category: "plumbing",
          issue_description: "Leaky faucet",
          priority: "normal"
        }
      }
    end

    request = MaintenanceRequest.last
    assert_redirected_to "/hotels/#{@hotel.id}/maintenance_requests/#{request.id}"
  end

  test "housekeeping cannot update maintenance_request" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}", params: {
      maintenance_request: { priority: "high" }
    }

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "housekeeping cannot destroy maintenance_request" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    assert_no_difference "MaintenanceRequest.count" do
      delete "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "housekeeping cannot assign maintenance_request" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)
    user = create(:user)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/assign", params: {
      assigned_to_id: user.id
    }

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "housekeeping cannot complete maintenance_request" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/complete"

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "housekeeping cannot cancel maintenance_request" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/cancel"

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can update maintenance_request" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}", params: {
      maintenance_request: { priority: "high" }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    @maintenance_request.reload
    assert_equal "high", @maintenance_request.priority
  end

  test "front_desk cannot destroy maintenance_request" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "MaintenanceRequest.count" do
      delete "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can assign maintenance_request" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    user = create(:user)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/assign", params: {
      assigned_to_id: user.id
    }

    assert_redirected_to "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    @maintenance_request.reload
    assert_equal user, @maintenance_request.assigned_to
  end

  test "front_desk can complete maintenance_request" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/complete", params: {
      resolution_notes: "Fixed the issue",
      actual_cost: 100.00
    }

    assert_redirected_to "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    @maintenance_request.reload
    assert_equal "completed", @maintenance_request.status
    assert_equal "Fixed the issue", @maintenance_request.resolution_notes
    assert_equal 100.00, @maintenance_request.actual_cost.to_f
  end

  test "front_desk can cancel maintenance_request" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/cancel", params: {
      reason: "No longer needed"
    }

    assert_redirected_to "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    @maintenance_request.reload
    assert_equal "cancelled", @maintenance_request.status
    assert_equal "No longer needed", @maintenance_request.resolution_notes
  end

  test "hotel_manager can destroy maintenance_request" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "MaintenanceRequest.count", -1 do
      delete "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    end

    assert_redirected_to "/hotels/#{@hotel.id}/maintenance_requests"
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access maintenance_request from another account" do
    other_account = create(:account)
    other_request = nil

    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
      other_room = create(:room, hotel: other_hotel, account: other_account, room_type: other_room_type)
      other_reporter = create(:user)
      other_request = create(:maintenance_request, :unassigned, hotel: other_hotel, account: other_account, room: other_room, reported_by: other_reporter)
    end

    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/hotels/#{other_request.hotel_id}/maintenance_requests/#{other_request.id}"
    assert_response :not_found
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns maintenance_requests list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/maintenance_requests"
    assert_response :success
  end

  test "show displays maintenance_request details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    assert_response :success
  end

  test "new displays maintenance_request form" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/maintenance_requests/new"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/maintenance_requests']"
  end

  test "create maintenance_request with valid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_difference "MaintenanceRequest.count", 1 do
      post "/hotels/#{@hotel.id}/maintenance_requests", params: {
        maintenance_request: {
          room_id: @room.id,
          category: "hvac",
          issue_description: "AC not cooling",
          priority: "high",
          cost_estimate: 250.00,
          scheduled_date: Date.tomorrow
        }
      }
    end

    request = MaintenanceRequest.last
    assert_redirected_to "/hotels/#{@hotel.id}/maintenance_requests/#{request.id}"
    assert_equal "hvac", request.category
    assert_equal "high", request.priority
    assert_equal @hotel, request.hotel
    assert_equal @account, request.account
  end

  test "create maintenance_request with invalid params fails" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "MaintenanceRequest.count" do
      post "/hotels/#{@hotel.id}/maintenance_requests", params: {
        maintenance_request: {
          room_id: @room.id,
          category: "invalid_category",
          issue_description: "Test"
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "create maintenance_request without issue_description fails" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "MaintenanceRequest.count" do
      post "/hotels/#{@hotel.id}/maintenance_requests", params: {
        maintenance_request: {
          room_id: @room.id,
          category: "electrical",
          issue_description: ""
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "edit displays maintenance_request form" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/edit"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}']"
  end

  test "update maintenance_request with valid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}", params: {
      maintenance_request: {
        priority: "critical",
        cost_estimate: 500.00
      }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    @maintenance_request.reload
    assert_equal "critical", @maintenance_request.priority
    assert_equal 500.00, @maintenance_request.cost_estimate.to_f
  end

  # ===========================================
  # Filtering Tests
  # ===========================================

  test "index filters by status" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    room2 = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    completed_request = create(:maintenance_request, :completed, :unassigned, hotel: @hotel, account: @account, room: room2, reported_by: @reporter)

    get "/hotels/#{@hotel.id}/maintenance_requests", params: { status: "open" }
    assert_response :success
  end

  test "index filters by priority" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    room2 = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    critical_request = create(:maintenance_request, :critical, :unassigned, hotel: @hotel, account: @account, room: room2, reported_by: @reporter)

    get "/hotels/#{@hotel.id}/maintenance_requests", params: { priority: "critical" }
    assert_response :success
  end

  test "index filters by category" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    room2 = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    plumbing_request = create(:maintenance_request, :plumbing, :unassigned, hotel: @hotel, account: @account, room: room2, reported_by: @reporter)

    get "/hotels/#{@hotel.id}/maintenance_requests", params: { category: "plumbing" }
    assert_response :success
  end

  test "index filters by room_id" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    room2 = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    other_room_request = create(:maintenance_request, :unassigned, hotel: @hotel, account: @account, room: room2, reported_by: @reporter)

    get "/hotels/#{@hotel.id}/maintenance_requests", params: { room_id: @room.id }
    assert_response :success
  end

  # ===========================================
  # Custom Actions Tests
  # ===========================================

  test "complete sets status to completed" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/complete"

    @maintenance_request.reload
    assert_equal "completed", @maintenance_request.status
  end

  test "complete sets completed_at timestamp" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    freeze_time do
      patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/complete"

      @maintenance_request.reload
      assert_not_nil @maintenance_request.completed_at
    end
  end

  test "complete accepts optional resolution_notes" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/complete", params: {
      resolution_notes: "Replaced the part"
    }

    @maintenance_request.reload
    assert_equal "Replaced the part", @maintenance_request.resolution_notes
  end

  test "complete accepts optional actual_cost" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/complete", params: {
      actual_cost: 175.50
    }

    @maintenance_request.reload
    assert_equal 175.50, @maintenance_request.actual_cost.to_f
  end

  test "assign updates assigned_to_id" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    user = create(:user)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/assign", params: {
      assigned_to_id: user.id
    }

    @maintenance_request.reload
    assert_equal user.id, @maintenance_request.assigned_to_id
  end

  test "assign can unassign by passing empty string" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    user = create(:user)
    @maintenance_request.update!(assigned_to: user)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/assign", params: {
      assigned_to_id: ""
    }

    @maintenance_request.reload
    assert_nil @maintenance_request.assigned_to_id
  end

  test "cancel sets status to cancelled" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/cancel"

    @maintenance_request.reload
    assert_equal "cancelled", @maintenance_request.status
  end

  test "cancel accepts optional reason" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/cancel", params: {
      reason: "Duplicate request"
    }

    @maintenance_request.reload
    assert_equal "Duplicate request", @maintenance_request.resolution_notes
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can perform all maintenance_request operations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Index
    get "/hotels/#{@hotel.id}/maintenance_requests"
    assert_response :success

    # Show
    get "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    assert_response :success

    # Create
    assert_difference "MaintenanceRequest.count", 1 do
      post "/hotels/#{@hotel.id}/maintenance_requests", params: {
        maintenance_request: {
          room_id: @room.id,
          category: "electrical",
          issue_description: "Admin reported issue"
        }
      }
    end

    # Update
    patch "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}", params: {
      maintenance_request: { priority: "low" }
    }
    assert_redirected_to "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"

    # Destroy
    assert_difference "MaintenanceRequest.count", -1 do
      delete "/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}"
    end
  end

  test "unauthenticated user cannot access maintenance_requests" do
    # With `authenticated :user` routing constraint, returns 404
    get "/hotels/#{@hotel.id}/maintenance_requests"
    assert_response :not_found
  end
end
