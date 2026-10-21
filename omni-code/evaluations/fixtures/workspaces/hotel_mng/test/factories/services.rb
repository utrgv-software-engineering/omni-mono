# == Schema Information
#
# Table name: services
#
#  id                       :bigint           not null, primary key
#  advance_booking_required :boolean          default(FALSE)
#  capacity                 :integer
#  category                 :string           not null
#  description              :text
#  duration_minutes         :integer
#  is_active                :boolean          default(TRUE)
#  name                     :string           not null
#  price                    :decimal(10, 2)   not null
#  pricing_unit             :string           default("fixed")
#  created_at               :datetime         not null
#  updated_at               :datetime         not null
#  account_id               :bigint           not null
#  hotel_id                 :bigint           not null
#
# Indexes
#
#  index_services_on_account_id                             (account_id)
#  index_services_on_account_id_and_hotel_id_and_is_active  (account_id,hotel_id,is_active)
#  index_services_on_hotel_id                               (hotel_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
FactoryBot.define do
  factory :service do
    association :account
    association :hotel
    sequence(:name) { |n| "Service #{n}" }
    category { Service::CATEGORIES.sample }
    description { "Premium service offering" }
    price { 50.00 }
    pricing_unit { 'fixed' }
    duration_minutes { 60 }
    capacity { 10 }
    advance_booking_required { false }
    is_active { true }

    trait :spa do
      name { "Spa Treatment" }
      category { 'spa' }
      price { 150.00 }
      duration_minutes { 90 }
      advance_booking_required { true }
    end

    trait :dining do
      name { "Restaurant Dining" }
      category { 'dining' }
      price { 75.00 }
      pricing_unit { 'per_person' }
      advance_booking_required { false }
    end

    trait :transport do
      name { "Airport Shuttle" }
      category { 'transport' }
      price { 35.00 }
      pricing_unit { 'per_person' }
      advance_booking_required { true }
    end

    trait :laundry do
      name { "Laundry Service" }
      category { 'laundry' }
      price { 5.00 }
      pricing_unit { 'per_item' }
      advance_booking_required { false }
    end

    trait :hourly do
      name { "Conference Room" }
      category { 'other' }
      price { 100.00 }
      pricing_unit { 'per_hour' }
      duration_minutes { nil }
    end

    trait :inactive do
      is_active { false }
    end

    trait :with_schedule do
      after(:create) do |service|
        (1..5).each do |day|
          create(:service_schedule, service: service, day_of_week: day)
        end
      end
    end
  end
end
