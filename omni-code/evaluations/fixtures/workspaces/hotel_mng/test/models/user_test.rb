# == Schema Information
#
# Table name: users
#
#  id                     :bigint           not null, primary key
#  current_sign_in_at     :datetime
#  current_sign_in_ip     :string
#  email                  :string           default(""), not null
#  encrypted_password     :string           default(""), not null
#  first_name             :string
#  force_password_change  :boolean          default(FALSE), not null
#  invitation_accepted_at :datetime
#  invitation_created_at  :datetime
#  invitation_limit       :integer
#  invitation_sent_at     :datetime
#  invitation_token       :string
#  invitations_count      :integer          default(0)
#  invited_by_type        :string
#  is_ai_agent            :boolean          default(FALSE), not null
#  is_platform_admin      :boolean          default(FALSE), not null
#  last_name              :string
#  last_sign_in_at        :datetime
#  last_sign_in_ip        :string
#  remember_created_at    :datetime
#  reset_password_sent_at :datetime
#  reset_password_token   :string
#  sign_in_count          :integer          default(0), not null
#  created_at             :datetime         not null
#  updated_at             :datetime         not null
#  invited_by_id          :bigint
#
# Indexes
#
#  index_users_on_email                 (email) UNIQUE
#  index_users_on_invitation_token      (invitation_token) UNIQUE
#  index_users_on_invited_by            (invited_by_type,invited_by_id)
#  index_users_on_invited_by_id         (invited_by_id)
#  index_users_on_is_ai_agent           (is_ai_agent)
#  index_users_on_last_sign_in_at       (last_sign_in_at)
#  index_users_on_reset_password_token  (reset_password_token) UNIQUE
#
require "test_helper"

class UserTest < ActiveSupport::TestCase
  test "should require first name" do
    user = User.new(
      email: "test@example.com",
      last_name: "Doe",
      password: "password123"
    )
    assert_not user.valid?
    assert_includes user.errors[:first_name], "can't be blank"
  end
  
  test "should require last name" do
    user = User.new(
      email: "test@example.com",
      first_name: "John",
      password: "password123"
    )
    assert_not user.valid?
    assert_includes user.errors[:last_name], "can't be blank"
  end
  
  test "full_name returns concatenated first and last name" do
    user = User.new(first_name: "John", last_name: "Doe")
    assert_equal "John Doe", user.full_name
  end
  
  test "display_name returns full name when available" do
    user = User.new(first_name: "John", last_name: "Doe", email: "john@example.com")
    assert_equal "John Doe", user.display_name
  end
  
  test "initials returns uppercase first letters of names" do
    user = User.new(first_name: "john", last_name: "doe")
    assert_equal "JD", user.initials
  end
  
  test "has_role? checks role for account user" do
    user = create(:user)
    account = create(:account)
    account_user = AccountUser.create!(user: user, account: account)
    
    # Add owner role
    account_user.add_role :owner
    
    user.current_account = account
    assert user.has_role?(:owner)
    assert_not user.has_role?(:admin)
  end
end
