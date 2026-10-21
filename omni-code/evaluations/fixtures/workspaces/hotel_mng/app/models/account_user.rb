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

class AccountUser < ApplicationRecord
  rolify
  belongs_to :account
  belongs_to :user
  
  # Priority 1 Roles
  PRIORITY_1_ROLES = %w[owner admin hotel_manager front_desk housekeeping].freeze
  
  # Role hierarchy for authorization
  ROLE_HIERARCHY = {
    owner: [:admin, :hotel_manager, :front_desk, :housekeeping],
    admin: [:hotel_manager, :front_desk, :housekeeping],
    hotel_manager: [:front_desk, :housekeeping],
    front_desk: [],
    housekeeping: []
  }.freeze
  
  # This model will have roles via Rolify
  # Roles are scoped to the account
  
  validates :user_id, uniqueness: { scope: :account_id }
  
  # API key generation for AI agents
  before_create :generate_api_key, if: :should_have_api_key?
  
  # Priority 1 Role checks
  def owner?
    has_role?(:owner)
  end
  
  def admin?
    has_role?(:admin)
  end
  
  def hotel_manager?
    has_role?(:hotel_manager)
  end
  
  def front_desk?
    has_role?(:front_desk)
  end
  
  def housekeeping?
    has_role?(:housekeeping)
  end
  
  # Check if user has any management role
  def management?
    owner? || admin? || hotel_manager?
  end
  
  # Check if user has any operational role
  def operational?
    front_desk? || housekeeping?
  end
  
  # Get the highest priority role for display purposes
  def primary_role
    return 'owner' if owner?
    return 'admin' if admin?
    return 'hotel_manager' if hotel_manager?
    return 'front_desk' if front_desk?
    return 'housekeeping' if housekeeping?
    'guest' # default if no role
  end
  
  # Human-friendly role name
  def role_display_name
    primary_role.humanize.titleize
  end
  
  private
  
  def should_have_api_key?
    user&.is_ai_agent?
  end
  
  def generate_api_key
    # Don't overwrite if api_key already set (e.g., for eval sessions)
    return if api_key.present?

    prefix = Rails.env.production? ? 'ak_live' : 'ak_test'
    self.api_key = "#{prefix}_#{SecureRandom.hex(24)}"
  end
end
