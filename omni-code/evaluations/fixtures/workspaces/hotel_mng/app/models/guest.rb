# == Schema Information
#
# Table name: guests
#
#  id              :bigint           not null, primary key
#  address         :string
#  city            :string
#  country         :string
#  date_of_birth   :date
#  email           :string           not null
#  first_name      :string           not null
#  id_number       :string
#  last_name       :string           not null
#  nationality     :string
#  notes           :text
#  passport_number :string
#  phone           :string
#  postal_code     :string
#  state           :string
#  vip_status      :boolean          default(FALSE)
#  created_at      :datetime         not null
#  updated_at      :datetime         not null
#  account_id      :bigint           not null
#
# Indexes
#
#  index_guests_on_account_id                (account_id)
#  index_guests_on_account_id_and_email      (account_id,email)
#  index_guests_on_account_id_and_last_name  (account_id,last_name)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#
class Guest < ApplicationRecord
  acts_as_tenant(:account)
  belongs_to :account
  has_many :reservations, dependent: :restrict_with_error
  has_many :service_bookings, dependent: :destroy
  
  validates :first_name, presence: true
  validates :last_name, presence: true
  validates :email, presence: true, format: { with: URI::MailTo::EMAIL_REGEXP }
  validates :phone, format: { with: /\A[\d\s\-\+\(\)]+\z/ }, allow_blank: true
  validates :vip_status, inclusion: { in: [true, false] }
  
  scope :vip, -> { where(vip_status: true) }
  scope :regular, -> { where(vip_status: false) }
  scope :with_reservations, -> { joins(:reservations).distinct }
  scope :search, ->(query) {
    where("LOWER(first_name) LIKE :query OR LOWER(last_name) LIKE :query OR LOWER(email) LIKE :query",
          query: "%#{query.downcase}%")
  }
  
  def full_name
    "#{first_name} #{last_name}"
  end
  
  def display_name
    vip_status? ? "#{full_name} (VIP)" : full_name
  end
  
  def display_initials
    "#{first_name.first}#{last_name.first}".upcase
  end
  
  def age
    return nil unless date_of_birth.present?
    ((Date.current - date_of_birth) / 365.25).floor
  end
  
  def adult?
    age.present? && age >= 18
  end
  
  def complete_address
    [address, city, state, country, postal_code].compact.reject(&:blank?).join(", ")
  end
  
  def total_stays
    reservations.where(status: ["checked_out", "checked_in"]).count
  end
  
  def total_spent
    reservations.where(status: "checked_out").sum(:total_amount)
  end
  
  def last_stay
    reservations.where(status: "checked_out")
                .order(check_out_date: :desc)
                .first
  end
  
  def upcoming_reservations
    reservations.where(status: ["pending", "confirmed"])
                .where("check_in_date >= ?", Date.current)
                .order(:check_in_date)
  end
  
  def current_reservation
    reservations.where(status: "checked_in").first
  end
  
  def loyalty_status
    case total_stays
    when 0..2
      "New"
    when 3..5
      "Regular"
    when 6..10
      "Frequent"
    else
      "Elite"
    end
  end
end
