# frozen_string_literal: true

require "test_helper"

class ApplicationSystemTestCase < ActionDispatch::SystemTestCase
  include FactoryBot::Syntax::Methods

  # Configure Selenium with headless Chrome
  # Use chromium binary available in Docker container
  driven_by :selenium, using: :headless_chrome, screen_size: [ 1400, 1400 ] do |options|
    # Required for running in Docker container
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
  end

  # Sign in helper for system tests - mirrors controller test helper pattern
  # @param role [Symbol] One of :owner, :admin, :hotel_manager, :front_desk, :housekeeping
  # @param account [Account] Optional - creates new if not provided
  # @param hotel [Hotel] Optional - creates new if not provided
  # @return [Hash] { user:, account:, hotel:, account_user: }
  def sign_in_as_role(role, account: nil, hotel: nil)
    account ||= create(:account, status: "active")
    ActsAsTenant.current_tenant = account

    hotel ||= create(:hotel, account: account)
    user = create(:user, password: "password123")
    account_user = AccountUser.create!(account: account, user: user)
    account_user.add_role(role)

    visit new_user_session_path
    fill_in "Email", with: user.email
    fill_in "Password", with: "password123"
    click_button "Log in"

    # Wait for successful login - should redirect to dashboard
    assert_text "Signed in successfully", wait: 5

    { user: user, account: account, hotel: hotel, account_user: account_user }
  end

  # Set up public context for guest-facing tests (no authentication)
  # @return [Hash] { account:, hotel:, room_type: }
  def setup_public_hotel
    account = create(:account)
    ActsAsTenant.current_tenant = account
    hotel = create(:hotel, account: account, subdomain: "testhotel")
    room_type = create(:room_type, hotel: hotel, account: account)
    create_list(:room, 3, hotel: hotel, room_type: room_type, status: "available", account: account)

    { account: account, hotel: hotel, room_type: room_type }
  end

  # Clean up tenant after each test
  teardown do
    ActsAsTenant.current_tenant = nil
  end
end
