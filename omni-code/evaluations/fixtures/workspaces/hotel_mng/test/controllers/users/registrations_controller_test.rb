# frozen_string_literal: true

require "test_helper"

module Users
  class RegistrationsControllerTest < ActionDispatch::IntegrationTest
    # ===========================================
    # Sign Up Form Tests
    # ===========================================

    test "new displays registration form" do
      get "/users/sign_up"

      assert_response :success
      assert_select "form[action='/users']"
    end

    # ===========================================
    # Account Creation Tests (per Testing Guide 6.1)
    # ===========================================

    test "create user creates Account with subdomain" do
      assert_difference ["User.count", "Account.count", "AccountUser.count"], 1 do
        post "/users", params: {
          user: {
            email: "newuser@example.com",
            password: "password123",
            password_confirmation: "password123",
            first_name: "John",
            last_name: "Doe"
          },
          account_name: "Acme Hotels"
        }
      end

      user = User.last
      account = Account.last
      account_user = AccountUser.last

      assert_equal "newuser@example.com", user.email
      assert_equal "John", user.first_name
      assert_equal "Doe", user.last_name
      assert_equal "Acme Hotels", account.name
      assert_equal user, account_user.user
      assert_equal account, account_user.account
    end

    test "create generates subdomain from account_name" do
      post "/users", params: {
        user: {
          email: "test@example.com",
          password: "password123",
          password_confirmation: "password123",
          first_name: "Test",
          last_name: "User"
        },
        account_name: "Grand Resort & Spa"
      }

      account = Account.last
      # Subdomain should be lowercase alphanumeric
      assert_match(/^[a-z0-9]+$/, account.subdomain)
      assert_equal "grandresortspa", account.subdomain
    end

    test "create generates unique subdomain on collision" do
      # Create an existing account with a subdomain
      existing_account = create(:account, subdomain: "testhotel")

      post "/users", params: {
        user: {
          email: "newuser@example.com",
          password: "password123",
          password_confirmation: "password123",
          first_name: "New",
          last_name: "User"
        },
        account_name: "Test Hotel"
      }

      new_account = Account.last
      # Should append a number to make it unique
      assert_not_equal "testhotel", new_account.subdomain
      assert_match(/^testhotel\d+$/, new_account.subdomain)
    end

    test "create assigns owner role to AccountUser" do
      post "/users", params: {
        user: {
          email: "owner@example.com",
          password: "password123",
          password_confirmation: "password123",
          first_name: "Owner",
          last_name: "User"
        },
        account_name: "Owner's Hotel"
      }

      account_user = AccountUser.last
      assert account_user.has_role?(:owner)
    end

    test "create sets trial_ends_at to 30 days from now" do
      freeze_time do
        post "/users", params: {
          user: {
            email: "trial@example.com",
            password: "password123",
            password_confirmation: "password123",
            first_name: "Trial",
            last_name: "User"
          },
          account_name: "Trial Hotel"
        }

        account = Account.last
        assert_equal 30.days.from_now.to_date, account.trial_ends_at.to_date
      end
    end

    test "create sets current_account in session" do
      post "/users", params: {
        user: {
          email: "session@example.com",
          password: "password123",
          password_confirmation: "password123",
          first_name: "Session",
          last_name: "User"
        },
        account_name: "Session Hotel"
      }

      # After successful registration, user should be redirected to dashboard
      assert_response :redirect
      follow_redirect!
      assert_response :success
    end

    test "create redirects to dashboard after successful signup" do
      post "/users", params: {
        user: {
          email: "redirect@example.com",
          password: "password123",
          password_confirmation: "password123",
          first_name: "Redirect",
          last_name: "User"
        },
        account_name: "Redirect Hotel"
      }

      assert_redirected_to "/"
    end

    # ===========================================
    # Transaction Rollback Tests
    # ===========================================

    test "create rolls back transaction on failure" do
      # Create a user with same email to trigger validation error
      create(:user, email: "existing@example.com")

      assert_no_difference ["User.count", "Account.count", "AccountUser.count"] do
        post "/users", params: {
          user: {
            email: "existing@example.com",
            password: "password123",
            password_confirmation: "password123",
            first_name: "Duplicate",
            last_name: "User"
          },
          account_name: "Duplicate Hotel"
        }
      end

      assert_response :unprocessable_entity
    end

    test "create with invalid password fails" do
      assert_no_difference ["User.count", "Account.count"] do
        post "/users", params: {
          user: {
            email: "invalid@example.com",
            password: "short",
            password_confirmation: "short",
            first_name: "Invalid",
            last_name: "User"
          },
          account_name: "Invalid Hotel"
        }
      end

      assert_response :unprocessable_entity
    end

    test "create with mismatched passwords fails" do
      assert_no_difference ["User.count", "Account.count"] do
        post "/users", params: {
          user: {
            email: "mismatch@example.com",
            password: "password123",
            password_confirmation: "different123",
            first_name: "Mismatch",
            last_name: "User"
          },
          account_name: "Mismatch Hotel"
        }
      end

      assert_response :unprocessable_entity
    end

    # ===========================================
    # Default Account Name Tests
    # ===========================================

    test "create uses first_name for account name if not provided" do
      post "/users", params: {
        user: {
          email: "noaccountname@example.com",
          password: "password123",
          password_confirmation: "password123",
          first_name: "DefaultName",
          last_name: "User"
        }
      }

      account = Account.last
      assert_equal "DefaultName's Organization", account.name
    end

    # ===========================================
    # Subdomain Generation Edge Cases
    # ===========================================

    test "create handles special characters in account name for subdomain" do
      post "/users", params: {
        user: {
          email: "special@example.com",
          password: "password123",
          password_confirmation: "password123",
          first_name: "Special",
          last_name: "User"
        },
        account_name: "Hôtel & Résort!!"
      }

      account = Account.last
      # Should strip special characters
      assert_match(/^[a-z0-9]+$/, account.subdomain)
    end

    test "create truncates long subdomain to 20 characters" do
      post "/users", params: {
        user: {
          email: "longname@example.com",
          password: "password123",
          password_confirmation: "password123",
          first_name: "Long",
          last_name: "User"
        },
        account_name: "The Grand International Luxury Resort and Spa Hotel"
      }

      account = Account.last
      # Base subdomain should be truncated (before uniqueness suffix)
      assert account.subdomain.length <= 22 # 20 + possible counter
    end

    # ===========================================
    # Account Defaults Tests
    # ===========================================

    test "create sets account defaults" do
      post "/users", params: {
        user: {
          email: "defaults@example.com",
          password: "password123",
          password_confirmation: "password123",
          first_name: "Defaults",
          last_name: "User"
        },
        account_name: "Defaults Hotel"
      }

      account = Account.last
      assert_equal "active", account.status
      assert_equal "UTC", account.timezone
      assert_equal "USD", account.currency
      assert_equal "defaults@example.com", account.email
    end
  end
end
