# frozen_string_literal: true

require "test_helper"

module Users
  class SessionsControllerTest < ActionDispatch::IntegrationTest
    setup do
      @account = create(:account, status: "active")
      @user = create(:user, email: "test@example.com", password: "password123")
      @account_user = AccountUser.create!(user: @user, account: @account)
      @account_user.add_role(:front_desk)
    end

    # ===========================================
    # Sign In Form Tests
    # ===========================================

    test "new displays sign in form" do
      get "/users/sign_in"

      assert_response :success
      assert_select "form[action='/users/sign_in']"
    end

    # ===========================================
    # Login Tests (per Testing Guide 6.2)
    # ===========================================

    test "create signs in user with valid credentials" do
      post "/users/sign_in", params: {
        user: {
          email: "test@example.com",
          password: "password123"
        }
      }

      assert_redirected_to "/"
      follow_redirect!
      assert_response :success
    end

    test "create sets current_account in session after login" do
      post "/users/sign_in", params: {
        user: {
          email: "test@example.com",
          password: "password123"
        }
      }

      assert_redirected_to "/"
      # After redirect, user should have access to dashboard
      follow_redirect!
      assert_response :success
    end

    test "create with invalid credentials fails" do
      post "/users/sign_in", params: {
        user: {
          email: "test@example.com",
          password: "wrongpassword"
        }
      }

      assert_response :unprocessable_entity
    end

    test "create with non-existent email fails" do
      post "/users/sign_in", params: {
        user: {
          email: "nonexistent@example.com",
          password: "password123"
        }
      }

      assert_response :unprocessable_entity
    end

    # ===========================================
    # Multiple Accounts Tests
    # ===========================================

    test "login selects first account for users with single account" do
      post "/users/sign_in", params: {
        user: {
          email: "test@example.com",
          password: "password123"
        }
      }

      assert_redirected_to "/"
      follow_redirect!
      assert_response :success
    end

    test "login selects first account for users with multiple accounts" do
      # Create a second account for the same user
      second_account = create(:account, status: "active")
      second_account_user = AccountUser.create!(user: @user, account: second_account)
      second_account_user.add_role(:admin)

      post "/users/sign_in", params: {
        user: {
          email: "test@example.com",
          password: "password123"
        }
      }

      assert_redirected_to "/"
      follow_redirect!
      assert_response :success
    end

    # ===========================================
    # No Active Account Tests
    # ===========================================

    test "login with no active accounts signs out and redirects" do
      # Remove the account user relationship
      @account_user.destroy!

      post "/users/sign_in", params: {
        user: {
          email: "test@example.com",
          password: "password123"
        }
      }

      # User should be signed out and redirected to sign in
      assert_redirected_to "/users/sign_in"
      follow_redirect!
      assert_match(/no active account/i, flash[:alert])
    end

    test "login with suspended account signs out and redirects" do
      # Mark the account as suspended
      @account.update!(status: "suspended")

      post "/users/sign_in", params: {
        user: {
          email: "test@example.com",
          password: "password123"
        }
      }

      # User should be signed out and redirected
      assert_redirected_to "/users/sign_in"
      follow_redirect!
      assert_match(/no active account/i, flash[:alert])
    end

    # ===========================================
    # Sign Out Tests
    # ===========================================

    test "destroy signs out user" do
      # First sign in
      post "/users/sign_in", params: {
        user: {
          email: "test@example.com",
          password: "password123"
        }
      }
      follow_redirect!

      # Then sign out
      delete "/users/sign_out"

      assert_response :redirect
    end

    test "destroy clears account from session" do
      # First sign in
      post "/users/sign_in", params: {
        user: {
          email: "test@example.com",
          password: "password123"
        }
      }
      follow_redirect!

      # Then sign out
      delete "/users/sign_out"
      follow_redirect!

      # Trying to access dashboard should redirect to sign in
      get "/"
      assert_response :redirect
    end

    # ===========================================
    # Session Persistence Tests
    # ===========================================

    test "authenticated user can access protected routes" do
      post "/users/sign_in", params: {
        user: {
          email: "test@example.com",
          password: "password123"
        }
      }
      follow_redirect!

      # Access dashboard
      get "/"
      assert_response :success

      # Access other protected routes
      get "/reservations"
      assert_response :success
    end

    test "unauthenticated user cannot access protected routes" do
      get "/reservations"
      assert_response :not_found
    end
  end
end
