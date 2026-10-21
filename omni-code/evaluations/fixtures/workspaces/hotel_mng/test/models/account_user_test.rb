# == Schema Information
#
# Table name: account_users
#
#  id                   :bigint           not null, primary key
#  api_key              :string
#  api_key_last_used_at :datetime
#  generated_password   :string
#  created_at           :datetime         not null
#  updated_at           :datetime         not null
#  account_id           :bigint           not null
#  user_id              :bigint           not null
#
# Indexes
#
#  index_account_users_on_account_id  (account_id)
#  index_account_users_on_api_key     (api_key) UNIQUE
#  index_account_users_on_user_id     (user_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (user_id => users.id)
#

require "test_helper"

class AccountUserTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    @user = create(:user)
    @account_user = AccountUser.create!(account: @account, user: @user)
  end

  # Role hierarchy tests (complex business logic per Testing Guide 4.1.1)

  test "primary_role returns owner when user has owner role" do
    @account_user.add_role(:owner)
    @account_user.add_role(:admin)
    @account_user.add_role(:front_desk)

    assert_equal "owner", @account_user.primary_role
  end

  test "primary_role returns admin when highest role is admin" do
    @account_user.add_role(:admin)
    @account_user.add_role(:hotel_manager)
    @account_user.add_role(:front_desk)

    assert_equal "admin", @account_user.primary_role
  end

  test "primary_role returns hotel_manager when highest role is hotel_manager" do
    @account_user.add_role(:hotel_manager)
    @account_user.add_role(:front_desk)

    assert_equal "hotel_manager", @account_user.primary_role
  end

  test "primary_role returns front_desk when only front_desk role" do
    @account_user.add_role(:front_desk)

    assert_equal "front_desk", @account_user.primary_role
  end

  test "primary_role returns housekeeping when only housekeeping role" do
    @account_user.add_role(:housekeeping)

    assert_equal "housekeeping", @account_user.primary_role
  end

  test "primary_role returns guest when no roles assigned" do
    assert_equal "guest", @account_user.primary_role
  end

  # Composite role check tests (complex business logic per Testing Guide 4.1.1)

  test "management? returns true for owner" do
    @account_user.add_role(:owner)
    assert @account_user.management?
  end

  test "management? returns true for admin" do
    @account_user.add_role(:admin)
    assert @account_user.management?
  end

  test "management? returns true for hotel_manager" do
    @account_user.add_role(:hotel_manager)
    assert @account_user.management?
  end

  test "management? returns false for front_desk" do
    @account_user.add_role(:front_desk)
    assert_not @account_user.management?
  end

  test "management? returns false for housekeeping" do
    @account_user.add_role(:housekeeping)
    assert_not @account_user.management?
  end

  test "operational? returns true for front_desk" do
    @account_user.add_role(:front_desk)
    assert @account_user.operational?
  end

  test "operational? returns true for housekeeping" do
    @account_user.add_role(:housekeeping)
    assert @account_user.operational?
  end

  test "operational? returns false for management roles" do
    @account_user.add_role(:owner)
    assert_not @account_user.operational?

    @account_user.remove_role(:owner)
    @account_user.add_role(:admin)
    assert_not @account_user.operational?
  end

  # API key generation tests (complex business logic per Testing Guide 4.1.1)

  test "generates api_key with test prefix for AI agent users" do
    ai_user = create(:user, is_ai_agent: true)
    ai_account_user = AccountUser.create!(account: @account, user: ai_user)

    assert_not_nil ai_account_user.api_key
    assert ai_account_user.api_key.start_with?("ak_test_")
  end

  test "does not generate api_key for regular users" do
    regular_user = create(:user, is_ai_agent: false)
    regular_account_user = AccountUser.create!(account: @account, user: regular_user)

    assert_nil regular_account_user.api_key
  end

  test "api_key is unique across account_users" do
    ai_user1 = create(:user, is_ai_agent: true)
    ai_user2 = create(:user, is_ai_agent: true)

    au1 = AccountUser.create!(account: @account, user: ai_user1)
    au2 = AccountUser.create!(account: @account, user: ai_user2)

    assert_not_equal au1.api_key, au2.api_key
  end

  # Role display name test (data transformation per Testing Guide 4.1.5)

  test "role_display_name formats role correctly" do
    @account_user.add_role(:hotel_manager)

    assert_equal "Hotel Manager", @account_user.role_display_name
  end
end
