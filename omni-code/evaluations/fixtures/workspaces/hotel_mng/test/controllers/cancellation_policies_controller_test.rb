# frozen_string_literal: true

require "test_helper"

class CancellationPoliciesControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @policy = create(:cancellation_policy, account: @account, name: "Test Policy", code: "TEST")
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping cannot access cancellation_policies index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/cancellation_policies"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can access cancellation_policies index" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/cancellation_policies"
    assert_response :success
  end

  test "front_desk can view cancellation_policy details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/cancellation_policies/#{@policy.id}"
    assert_response :success
  end

  test "front_desk cannot create cancellation_policy" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/cancellation_policies/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot update cancellation_policy" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/cancellation_policies/#{@policy.id}", params: {
      cancellation_policy: { name: "Updated Name" }
    }

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot destroy cancellation_policy" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "CancellationPolicy.count" do
      delete "/cancellation_policies/#{@policy.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager can create cancellation_policy" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "CancellationPolicy.count", 1 do
      post "/cancellation_policies", params: {
        cancellation_policy: {
          name: "New Policy",
          code: "NEWP",
          cancellation_type: "moderate",
          free_cancellation_hours: 48
        }
      }
    end

    policy = CancellationPolicy.last
    assert_redirected_to "/cancellation_policies/#{policy.id}"
  end

  test "hotel_manager can update cancellation_policy" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/cancellation_policies/#{@policy.id}", params: {
      cancellation_policy: { name: "Updated Policy", description: "New terms" }
    }

    assert_redirected_to "/cancellation_policies/#{@policy.id}"
    @policy.reload
    assert_equal "Updated Policy", @policy.name
    assert_equal "New terms", @policy.description
  end

  test "hotel_manager can destroy cancellation_policy" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "CancellationPolicy.count", -1 do
      delete "/cancellation_policies/#{@policy.id}"
    end

    assert_redirected_to "/cancellation_policies"
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns cancellation_policies list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/cancellation_policies"
    assert_response :success
  end

  test "index filters by account_wide" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    # The default policy has no hotel (account-wide)
    hotel_policy = create(:cancellation_policy, :with_hotel, account: @account, hotel: @hotel, code: "HOTEL")

    get "/cancellation_policies", params: { filter: "account_wide" }
    assert_response :success
  end

  test "index filters by hotel_specific" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    hotel_policy = create(:cancellation_policy, :with_hotel, account: @account, hotel: @hotel, code: "HOTEL2")

    get "/cancellation_policies", params: { filter: "hotel_specific" }
    assert_response :success
  end

  test "show displays cancellation_policy details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/cancellation_policies/#{@policy.id}"
    assert_response :success
  end

  test "new displays cancellation_policy form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/cancellation_policies/new"
    assert_response :success
    assert_select "form[action='/cancellation_policies']"
  end

  test "create cancellation_policy with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "CancellationPolicy.count", 1 do
      post "/cancellation_policies", params: {
        cancellation_policy: {
          name: "Strict Policy",
          code: "STRCT",
          cancellation_type: "strict",
          free_cancellation_hours: 168,
          penalty_percentage: 50,
          no_show_penalty: 100,
          is_refundable: true,
          description: "Strict cancellation terms"
        }
      }
    end

    policy = CancellationPolicy.last
    assert_redirected_to "/cancellation_policies/#{policy.id}"
    assert_equal "Strict Policy", policy.name
    assert_equal "strict", policy.cancellation_type
    assert_equal @account, policy.account
  end

  test "create cancellation_policy with invalid params fails" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_no_difference "CancellationPolicy.count" do
      post "/cancellation_policies", params: {
        cancellation_policy: {
          name: "", # Name is required
          code: "INV",
          cancellation_type: "flexible"
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "edit displays cancellation_policy form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/cancellation_policies/#{@policy.id}/edit"
    assert_response :success
    assert_select "form[action='/cancellation_policies/#{@policy.id}']"
  end

  test "update cancellation_policy with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/cancellation_policies/#{@policy.id}", params: {
      cancellation_policy: {
        penalty_percentage: 25,
        is_default: true
      }
    }

    assert_redirected_to "/cancellation_policies/#{@policy.id}"
    @policy.reload
    assert_equal 25, @policy.penalty_percentage.to_i
    assert @policy.is_default
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access cancellation_policy from another account" do
    other_account = create(:account)
    other_policy = nil

    ActsAsTenant.with_tenant(other_account) do
      other_policy = create(:cancellation_policy, account: other_account)
    end

    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/cancellation_policies/#{other_policy.id}"
    assert_response :not_found
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can perform all cancellation_policy operations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Index
    get "/cancellation_policies"
    assert_response :success

    # Show
    get "/cancellation_policies/#{@policy.id}"
    assert_response :success

    # Create
    assert_difference "CancellationPolicy.count", 1 do
      post "/cancellation_policies", params: {
        cancellation_policy: {
          name: "Admin Policy",
          code: "ADMIN",
          cancellation_type: "flexible"
        }
      }
    end

    # Update
    patch "/cancellation_policies/#{@policy.id}", params: {
      cancellation_policy: { description: "Admin updated" }
    }
    assert_redirected_to "/cancellation_policies/#{@policy.id}"

    # Destroy
    assert_difference "CancellationPolicy.count", -1 do
      delete "/cancellation_policies/#{@policy.id}"
    end
  end

  test "unauthenticated user cannot access cancellation_policies" do
    get "/cancellation_policies"
    assert_response :not_found
  end
end
