# == Schema Information
#
# Table name: hotel_policies
#
#  id          :bigint           not null, primary key
#  description :text             not null
#  is_active   :boolean          default(TRUE)
#  policy_type :string           not null
#  sort_order  :integer          default(0)
#  title       :string           not null
#  created_at  :datetime         not null
#  updated_at  :datetime         not null
#  account_id  :bigint           not null
#  hotel_id    :bigint           not null
#
# Indexes
#
#  index_hotel_policies_on_account_hotel_type_active  (account_id,hotel_id,policy_type,is_active)
#  index_hotel_policies_on_account_id                 (account_id)
#  index_hotel_policies_on_hotel_id                   (hotel_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
FactoryBot.define do
  factory :hotel_policy do
    association :account
    association :hotel
    policy_type { HotelPolicy::POLICY_TYPES.sample }
    sequence(:title) { |n| "Policy #{n}" }
    description { "This is a detailed policy description outlining the rules and guidelines." }
    sort_order { 0 }
    is_active { true }
    
    trait :check_in do
      policy_type { "check_in" }
      title { "Check-in Policy" }
      description { "Check-in is available from 3:00 PM. Early check-in may be available upon request." }
    end
    
    trait :check_out do
      policy_type { "check_out" }
      title { "Check-out Policy" }
      description { "Check-out is at 11:00 AM. Late check-out may be available for an additional fee." }
    end
    
    trait :cancellation do
      policy_type { "cancellation" }
      title { "Cancellation Policy" }
      description { "Free cancellation up to 24 hours before check-in. Late cancellations will be charged one night." }
    end
    
    trait :pet do
      policy_type { "pet" }
      title { "Pet Policy" }
      description { "Pets are welcome with a $50 per night fee. Maximum 2 pets per room." }
    end
    
    trait :smoking do
      policy_type { "smoking" }
      title { "Smoking Policy" }
      description { "This is a non-smoking property. Smoking is only permitted in designated outdoor areas." }
    end
    
    trait :children do
      policy_type { "children" }
      title { "Children Policy" }
      description { "Children of all ages are welcome. Children under 12 stay free when using existing bedding." }
    end
    
    trait :inactive do
      is_active { false }
    end
    
    trait :high_priority do
      sort_order { 1 }
    end
    
    trait :low_priority do
      sort_order { 10 }
    end
  end
end
