# == Schema Information
#
# Table name: cancellation_policies
#
#  id                      :bigint           not null, primary key
#  cancellation_type       :string           not null
#  code                    :string           not null
#  description             :text
#  free_cancellation_hours :integer
#  is_default              :boolean          default(FALSE)
#  is_refundable           :boolean          default(TRUE)
#  name                    :string           not null
#  no_show_penalty         :decimal(5, 2)
#  penalty_nights          :integer
#  penalty_percentage      :decimal(5, 2)
#  created_at              :datetime         not null
#  updated_at              :datetime         not null
#  account_id              :bigint           not null
#  hotel_id                :bigint
#
# Indexes
#
#  index_cancellation_policies_on_account_hotel_default  (account_id,hotel_id,is_default)
#  index_cancellation_policies_on_account_id             (account_id)
#  index_cancellation_policies_on_hotel_id               (hotel_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
class CancellationPolicy < ApplicationRecord
  acts_as_tenant(:account)
  
  belongs_to :account
  belongs_to :hotel, optional: true
  # has_many :rate_plans # TODO: Uncomment when rate_plans model is implemented
  
  CANCELLATION_TYPES = %w[flexible moderate strict non_refundable].freeze
  
  validates :name, presence: true
  validates :code, presence: true
  validates :cancellation_type, presence: true, inclusion: { in: CANCELLATION_TYPES }
  validates :free_cancellation_hours, numericality: { greater_than_or_equal_to: 0 }, allow_nil: true
  validates :penalty_percentage, numericality: { greater_than_or_equal_to: 0, less_than_or_equal_to: 100 }, allow_nil: true
  validates :penalty_nights, numericality: { greater_than_or_equal_to: 0 }, allow_nil: true
  validates :no_show_penalty, numericality: { greater_than_or_equal_to: 0, less_than_or_equal_to: 100 }, allow_nil: true
  
  scope :account_wide, -> { where(hotel_id: nil) }
  scope :hotel_specific, -> { where.not(hotel_id: nil) }
  scope :default, -> { where(is_default: true) }
  scope :refundable, -> { where(is_refundable: true) }
  
  before_save :ensure_single_default_per_scope
  
  def calculate_cancellation_fee(reservation, cancellation_date = Date.current)
    return 0 unless reservation
    
    cancellation_date = cancellation_date.to_date if cancellation_date.respond_to?(:to_date)
    days_until_checkin = (reservation.check_in_date.to_date - cancellation_date).to_i
    
    case cancellation_type
    when 'flexible'
      calculate_flexible_fee(reservation, days_until_checkin)
    when 'moderate'
      calculate_moderate_fee(reservation, days_until_checkin)
    when 'strict'
      calculate_strict_fee(reservation, days_until_checkin)
    when 'non_refundable'
      reservation.paid_amount
    else
      0
    end
  end
  
  def calculate_no_show_fee(reservation)
    return 0 unless reservation && no_show_penalty
    
    (reservation.total_amount * no_show_penalty / 100).round(2)
  end
  
  def make_default!
    transaction do
      if hotel_id.present?
        CancellationPolicy.where(account: account, hotel: hotel, is_default: true)
                         .where.not(id: id)
                         .update_all(is_default: false)
      else
        CancellationPolicy.where(account: account, hotel_id: nil, is_default: true)
                         .where.not(id: id)
                         .update_all(is_default: false)
      end
      update!(is_default: true)
    end
  end
  
  private
  
  def ensure_single_default_per_scope
    return unless is_default? && is_default_changed?
    
    if hotel_id.present?
      CancellationPolicy.where(account: account, hotel: hotel, is_default: true)
                       .where.not(id: id)
                       .update_all(is_default: false)
    else
      CancellationPolicy.where(account: account, hotel_id: nil, is_default: true)
                       .where.not(id: id)
                       .update_all(is_default: false)
    end
  end
  
  def calculate_flexible_fee(reservation, days_until_checkin)
    hours_until_checkin = days_until_checkin * 24
    if free_cancellation_hours && hours_until_checkin > free_cancellation_hours
      0
    elsif penalty_nights
      (reservation.total_amount.to_f / reservation.nights) * penalty_nights
    elsif penalty_percentage
      reservation.total_amount * penalty_percentage / 100
    else
      0
    end
  end
  
  def calculate_moderate_fee(reservation, days_until_checkin)
    if days_until_checkin >= 5
      0
    elsif days_until_checkin >= 1
      reservation.total_amount.to_f / reservation.nights
    else
      reservation.total_amount
    end
  end
  
  def calculate_strict_fee(reservation, days_until_checkin)
    if days_until_checkin >= 7
      reservation.total_amount * 0.5
    else
      reservation.total_amount
    end
  end
end
