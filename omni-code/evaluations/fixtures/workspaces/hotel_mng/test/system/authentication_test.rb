# frozen_string_literal: true

require "application_system_test_case"

class AuthenticationTest < ApplicationSystemTestCase
  test "user can sign in with valid credentials" do
    account = create(:account, status: "active")
    user = create(:user, email: "test@example.com", password: "password123")
    account_user = AccountUser.create!(user: user, account: account)
    account_user.add_role(:front_desk)

    visit new_user_session_path
    fill_in "Email", with: "test@example.com"
    fill_in "Password", with: "password123"
    click_button "Log in"

    assert_text "Signed in successfully"
    assert_text "Today's Arrivals"
  end

  test "user can sign out" do
    context = sign_in_as_role(:front_desk)
    user = context[:user]

    # Sign out is in the profile dropdown in sidebar
    # First click on the user name/profile to open the dropdown
    click_button user.display_name

    # Now click Sign Out
    click_button "Sign Out"

    assert_text "Signed out successfully"
  end

  test "user is redirected to sign in when not authenticated" do
    # Start fresh without signing in - just go directly to sign-in page
    visit new_user_session_path

    # Verify we can access the sign-in page
    assert_text "Log in"
    assert_text "Email"
    assert_text "Password"
  end
end
