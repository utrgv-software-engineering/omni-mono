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
class User < ApplicationRecord
  # Include default devise modules. Others available are:
  # :confirmable, :lockable, :timeoutable, :trackable and :omniauthable
  devise :invitable, :database_authenticatable, :registerable,
         :recoverable, :rememberable, :validatable, :trackable
  
  has_many :account_users, dependent: :destroy
  has_many :accounts, through: :account_users
  
  # For current session context
  attr_accessor :current_account
  
  # Name validations
  validates :first_name, presence: true, length: { maximum: 50 }
  validates :last_name, presence: true, length: { maximum: 50 }
  
  # Full name helper
  def full_name
    "#{first_name} #{last_name}".strip
  end
  
  # Display name (full name or email if names not available)
  def display_name
    full_name.presence || email
  end
  
  # Initials for avatar display
  def initials
    "#{first_name&.first}#{last_name&.first}".upcase
  end
  
  def has_role?(role_name, account = nil)
    account ||= current_account
    return false unless account

    account_user = account_users.find_by(account: account)
    return false unless account_user

    account_user.has_role?(role_name)
  end

  # Platform admin check
  def platform_admin?
    is_platform_admin
  end
end
