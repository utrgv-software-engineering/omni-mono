# frozen_string_literal: true

require "test_helper"

class AccountUsersControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)

    # Create an existing user in the account for testing
    @existing_user = create(:user)
    @existing_account_user = @account.account_users.create!(user: @existing_user)
    @existing_account_user.add_role(:front_desk)
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping cannot access account_users index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/account_users"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot access account_users index" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/account_users"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager cannot access account_users index" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/account_users"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "admin can access account_users index" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    get "/account_users"
    assert_response :success
  end

  test "owner can access account_users index" do
    sign_in_as(:owner, account: @account, hotel: @hotel)

    get "/account_users"
    assert_response :success
  end

  test "admin can view new form" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    get "/account_users/new"
    assert_response :success
  end

  test "owner can view new form" do
    sign_in_as(:owner, account: @account, hotel: @hotel)

    get "/account_users/new"
    assert_response :success
  end

  test "front_desk cannot view new form" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/account_users/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  # ===========================================
  # User Creation Tests
  # ===========================================

  test "admin can add existing user to account" do
    sign_in_as(:admin, account: @account, hotel: @hotel)
    new_user = create(:user)

    initial_count = @account.account_users.count

    post "/account_users", params: {
      user_type: "human",
      email: new_user.email,
      role: "front_desk"
    }

    assert_redirected_to "/account_users"
    assert_equal initial_count + 1, @account.account_users.count

    account_user = @account.account_users.find_by(user: new_user)
    assert account_user.present?
    assert account_user.front_desk?
  end

  test "admin can invite new user" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    initial_user_count = User.count
    initial_account_user_count = @account.account_users.count

    post "/account_users", params: {
      user_type: "human",
      email: "newuser@example.com",
      first_name: "New",
      last_name: "User",
      role: "hotel_manager"
    }

    assert_redirected_to "/account_users"
    assert_equal initial_user_count + 1, User.count
    assert_equal initial_account_user_count + 1, @account.account_users.count

    new_user = User.find_by(email: "newuser@example.com")
    assert new_user.present?
    assert new_user.invitation_sent_at.present?
  end

  test "admin can create AI agent" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    initial_user_count = User.count
    initial_account_user_count = @account.account_users.count

    post "/account_users", params: {
      user_type: "ai_agent",
      agent_name: "Test Bot",
      agent_description: "A test AI agent",
      role: "front_desk"
    }

    assert_redirected_to "/account_users"
    assert_match(/AI Agent.*created successfully/i, flash[:notice])

    assert_equal initial_user_count + 1, User.count
    assert_equal initial_account_user_count + 1, @account.account_users.count

    # AI agent email format is name@subdomain.ai
    ai_user = User.find_by(email: "test-bot@#{@account.subdomain}.ai")
    assert ai_user.present?
    assert ai_user.is_ai_agent

    # AI agent should have API key
    account_user = @account.account_users.find_by(user: ai_user)
    assert account_user.api_key.present?
  end

  test "create fails if role is blank" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    post "/account_users", params: {
      user_type: "human",
      email: "test@example.com",
      role: ""
    }

    assert_redirected_to "/account_users/new"
    assert_match(/role is required/i, flash[:alert])
  end

  test "create fails if email is blank for human user" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    post "/account_users", params: {
      user_type: "human",
      email: "",
      role: "front_desk"
    }

    assert_redirected_to "/account_users/new"
    assert_match(/email is required/i, flash[:alert])
  end

  test "create fails if agent name is blank for AI agent" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    post "/account_users", params: {
      user_type: "ai_agent",
      agent_name: "",
      role: "front_desk"
    }

    assert_redirected_to "/account_users/new"
    assert_match(/agent name is required/i, flash[:alert])
  end

  test "create fails if user is already a member" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    post "/account_users", params: {
      user_type: "human",
      email: @existing_user.email,
      role: "hotel_manager"
    }

    assert_redirected_to "/account_users"
    assert_match(/already a member/i, flash[:alert])
  end

  test "create fails if AI agent with same name exists" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # First, create an AI agent
    ai_user = create(:user, email: "duplicate-bot@#{@account.subdomain}.ai", is_ai_agent: true)

    post "/account_users", params: {
      user_type: "ai_agent",
      agent_name: "Duplicate Bot",
      role: "front_desk"
    }

    assert_redirected_to "/account_users/new"
    assert_match(/already exists/i, flash[:alert])
  end

  # ===========================================
  # Role Update Tests
  # ===========================================

  test "admin can edit account_user" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    get "/account_users/#{@existing_account_user.id}/edit"
    assert_response :success
  end

  test "admin can update account_user role" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    patch "/account_users/#{@existing_account_user.id}", params: {
      role: "hotel_manager"
    }

    assert_redirected_to "/account_users"
    @existing_account_user.reload
    assert @existing_account_user.hotel_manager?
    assert_not @existing_account_user.front_desk?
  end

  test "update fails with invalid role" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    patch "/account_users/#{@existing_account_user.id}", params: {
      role: "invalid_role"
    }

    assert_redirected_to "/account_users/#{@existing_account_user.id}/edit"
    assert_match(/invalid role/i, flash[:alert])
    @existing_account_user.reload
    assert @existing_account_user.front_desk? # Still has original role
  end

  # ===========================================
  # Destroy Tests
  # ===========================================

  test "admin can destroy account_user" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    initial_count = @account.account_users.count

    delete "/account_users/#{@existing_account_user.id}"

    assert_redirected_to "/account_users"
    assert_equal initial_count - 1, @account.account_users.count
    assert_match(/removed from the account/i, flash[:notice])
  end

  test "cannot remove yourself from account" do
    result = sign_in_as(:admin, account: @account, hotel: @hotel)

    admin_account_user = result[:account_user]
    initial_count = @account.account_users.count

    delete "/account_users/#{admin_account_user.id}"

    assert_redirected_to "/account_users"
    assert_match(/cannot remove yourself/i, flash[:alert])
    assert_equal initial_count, @account.account_users.count
  end

  test "cannot remove last owner from account" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Create an owner
    owner_user = create(:user)
    owner_account_user = @account.account_users.create!(user: owner_user)
    owner_account_user.add_role(:owner)

    initial_count = @account.account_users.count

    delete "/account_users/#{owner_account_user.id}"

    assert_redirected_to "/account_users"
    assert_match(/last owner/i, flash[:alert])
    assert_equal initial_count, @account.account_users.count
  end

  test "can remove owner if there is another owner" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Create two owners
    owner_user1 = create(:user)
    owner_account_user1 = @account.account_users.create!(user: owner_user1)
    owner_account_user1.add_role(:owner)

    owner_user2 = create(:user)
    owner_account_user2 = @account.account_users.create!(user: owner_user2)
    owner_account_user2.add_role(:owner)

    initial_count = @account.account_users.count

    delete "/account_users/#{owner_account_user1.id}"

    assert_redirected_to "/account_users"
    assert_match(/removed from the account/i, flash[:notice])
    assert_equal initial_count - 1, @account.account_users.count
  end

  test "front_desk cannot destroy account_user" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    delete "/account_users/#{@existing_account_user.id}"

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access account_user from another account" do
    other_account = create(:account)
    other_account_user = nil

    ActsAsTenant.with_tenant(other_account) do
      other_user = create(:user)
      other_account_user = other_account.account_users.create!(user: other_user)
      other_account_user.add_role(:front_desk)
    end

    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/account_users/#{other_account_user.id}/edit"
    assert_response :not_found
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "owner can perform all account_user operations" do
    sign_in_as(:owner, account: @account, hotel: @hotel)

    # Index
    get "/account_users"
    assert_response :success

    # New
    get "/account_users/new"
    assert_response :success

    # Create
    new_user = create(:user)
    post "/account_users", params: {
      user_type: "human",
      email: new_user.email,
      role: "front_desk"
    }
    assert_redirected_to "/account_users"

    # Edit
    get "/account_users/#{@existing_account_user.id}/edit"
    assert_response :success

    # Update
    patch "/account_users/#{@existing_account_user.id}", params: {
      role: "hotel_manager"
    }
    assert_redirected_to "/account_users"

    # Destroy
    delete "/account_users/#{@existing_account_user.id}"
    assert_redirected_to "/account_users"
  end

  test "unauthenticated user cannot access account_users" do
    # With `authenticated :user` routing constraint, returns 404
    get "/account_users"
    assert_response :not_found
  end
end
