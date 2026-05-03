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
FactoryBot.define do
  factory :cancellation_policy do
    association :account
    hotel { nil }
    sequence(:name) { |n| "Cancellation Policy #{n}" }
    sequence(:code) { |n| "CANCEL#{n}" }
    description { "Standard cancellation policy terms and conditions." }
    cancellation_type { "flexible" }
    free_cancellation_hours { 24 }
    penalty_percentage { nil }
    penalty_nights { nil }
    no_show_penalty { 100 }
    is_refundable { true }
    is_default { false }
    
    trait :flexible do
      cancellation_type { "flexible" }
      name { "Flexible Cancellation" }
      code { "FLEX" }
      description { "Free cancellation up to 24 hours before check-in." }
      free_cancellation_hours { 24 }
      penalty_nights { 1 }
    end
    
    trait :moderate do
      cancellation_type { "moderate" }
      name { "Moderate Cancellation" }
      code { "MOD" }
      description { "Free cancellation up to 5 days before check-in. One night charge for late cancellations." }
      free_cancellation_hours { 120 }
      penalty_nights { 1 }
    end
    
    trait :strict do
      cancellation_type { "strict" }
      name { "Strict Cancellation" }
      code { "STRICT" }
      description { "50% refund for cancellations made 7+ days before check-in. No refund after." }
      free_cancellation_hours { 168 }
      penalty_percentage { 50 }
    end
    
    trait :non_refundable do
      cancellation_type { "non_refundable" }
      name { "Non-Refundable Rate" }
      code { "NONREF" }
      description { "This rate is non-refundable and cannot be cancelled or modified." }
      free_cancellation_hours { nil }
      penalty_percentage { 100 }
      is_refundable { false }
    end
    
    trait :with_hotel do
      association :hotel
    end
    
    trait :account_wide do
      hotel { nil }
    end
    
    trait :default do
      is_default { true }
    end
    
    trait :no_penalty do
      penalty_percentage { nil }
      penalty_nights { nil }
      no_show_penalty { 0 }
    end
  end
end
