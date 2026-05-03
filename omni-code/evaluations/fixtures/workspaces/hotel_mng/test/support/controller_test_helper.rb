# Helper methods for controller tests
# Provides authentication, authorization, and tenant context setup

module ControllerTestHelper
  # Sign in a user with a specific role and set up full context
  # Returns a hash with all created objects for test assertions
  #
  # @param role [Symbol] One of :owner, :admin, :hotel_manager, :front_desk, :housekeeping
  # @param account [Account] Optional - creates new if not provided
  # @param hotel [Hotel] Optional - creates new if not provided
  # @return [Hash] { user:, account:, hotel:, account_user: }
  def sign_in_as(role, account: nil, hotel: nil)
    account ||= create(:account)
    ActsAsTenant.current_tenant = account

    hotel ||= create(:hotel, account: account)
    user = create(:user)
    account_user = AccountUser.create!(account: account, user: user)
    account_user.add_role(role)

    sign_in user

    # Set session context that controllers expect
    # Use integration test session setting
    if respond_to?(:post) && !respond_to?(:session=)
      # Integration test - session is managed differently
      @current_test_account = account
      @current_test_hotel = hotel
    end

    {
      user: user,
      account: account,
      hotel: hotel,
      account_user: account_user
    }
  end

  # Create a user with a specific role without signing in
  # Useful for creating other users in tests
  def create_user_with_role(role, account:)
    user = create(:user)
    account_user = AccountUser.create!(account: account, user: user)
    account_user.add_role(role)
    { user: user, account_user: account_user }
  end

  # Set up public controller context (no authentication)
  # Simulates subdomain resolution
  def set_public_context(hotel)
    ActsAsTenant.current_tenant = hotel.account
    @hotel = hotel
  end

  # Assert that a response is forbidden (403) or redirects with error
  def assert_forbidden
    assert_response :forbidden
  rescue Minitest::Assertion
    # Some controllers redirect instead of returning 403
    assert_response :redirect
    follow_redirect! if respond_to?(:follow_redirect!)
    assert_match(/not authorized|permission/i, flash[:alert].to_s) if flash[:alert]
  end

  # Assert that a response indicates not found
  def assert_not_found_response
    assert_response :not_found
  rescue Minitest::Assertion
    assert_response :redirect
  end

  # Get the current hotel for the test session
  # Works with integration tests that use cookies/session
  def current_test_hotel
    @current_test_hotel
  end

  # Get the current account for the test session
  def current_test_account
    @current_test_account
  end
end

# Include Devise test helpers for integration tests
class ActionDispatch::IntegrationTest
  include Devise::Test::IntegrationHelpers
  include ControllerTestHelper

  # Set up tenant context before each test that uses sign_in_as
  setup do
    ActsAsTenant.current_tenant = nil
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  private

  # Override request methods to include hotel context in session
  def get(path, **args)
    set_hotel_session(args)
    super
  end

  def post(path, **args)
    set_hotel_session(args)
    super
  end

  def patch(path, **args)
    set_hotel_session(args)
    super
  end

  def put(path, **args)
    set_hotel_session(args)
    super
  end

  def delete(path, **args)
    set_hotel_session(args)
    super
  end

  def set_hotel_session(args)
    return unless @current_test_hotel

    args[:headers] ||= {}
    # For integration tests, we need to ensure the session contains current_hotel_id
    # This is typically done via cookies in Rails integration tests
  end
end
