# == Schema Information
#
# Table name: accounts
#
#  id            :bigint           not null, primary key
#  address       :string
#  city          :string
#  country       :string
#  currency      :string           default("USD")
#  email         :string
#  name          :string
#  phone         :string
#  postal_code   :string
#  state         :string
#  status        :string           default("active")
#  subdomain     :string
#  timezone      :string           default("UTC")
#  trial_ends_at :datetime
#  created_at    :datetime         not null
#  updated_at    :datetime         not null
#
# Indexes
#
#  index_accounts_on_subdomain  (subdomain) UNIQUE
#
class Account < ApplicationRecord
  has_many :hotels, dependent: :destroy
  has_many :account_users, dependent: :destroy
  has_many :users, through: :account_users
  has_many :room_types, dependent: :destroy
  has_many :rooms, dependent: :destroy
  has_many :guests, dependent: :destroy
  has_many :reservations, dependent: :destroy
  has_many :amenities, dependent: :destroy
  has_many :hotel_amenities, dependent: :destroy
  has_many :room_type_amenities, dependent: :destroy
  has_many :hotel_policies, dependent: :destroy
  has_many :cancellation_policies, dependent: :destroy
  has_many :housekeeping_tasks, dependent: :destroy
  has_many :maintenance_requests, dependent: :destroy
  
  validates :name, presence: true
  validates :subdomain, presence: true, uniqueness: true,
            format: { with: /\A[a-z0-9]+\z/, message: "only lowercase letters and numbers allowed" }
  validates :email, format: { with: URI::MailTo::EMAIL_REGEXP }, allow_blank: true
  validates :status, inclusion: { in: %w[active suspended cancelled] }, allow_blank: true
  validates :currency, inclusion: { in: %w[USD EUR GBP CAD AUD] }, allow_blank: true
  validates :timezone, inclusion: { in: ActiveSupport::TimeZone.all.map(&:name) }, allow_blank: true
  
  scope :active, -> { where(status: "active") }
  scope :trial, -> { where("trial_ends_at > ?", Time.current) }
  
  # Account is the tenant model, not a tenanted model
  # Remove acts_as_tenant from here
  
  def trial_active?
    trial_ends_at.present? && trial_ends_at > Time.current
  end
  
  def suspended?
    status == "suspended"
  end
  
  def cancelled?
    status == "cancelled"
  end
end
