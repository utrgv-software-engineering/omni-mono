# == Schema Information
#
# Table name: room_types
#
#  id                  :bigint           not null, primary key
#  base_price          :decimal(10, 2)   not null
#  code                :string           not null
#  description         :text
#  extra_person_charge :decimal(10, 2)   default(0.0)
#  max_occupancy       :integer          not null
#  name                :string           not null
#  size_sqm            :decimal(, )
#  standard_occupancy  :integer          not null
#  created_at          :datetime         not null
#  updated_at          :datetime         not null
#  account_id          :bigint           not null
#  hotel_id            :bigint           not null
#
# Indexes
#
#  index_room_types_on_account_id         (account_id)
#  index_room_types_on_hotel_id           (hotel_id)
#  index_room_types_on_hotel_id_and_code  (hotel_id,code) UNIQUE
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
FactoryBot.define do
  factory :room_type do
    association :account
    association :hotel
    sequence(:name) { |n| "Room Type #{n}" }
    sequence(:code) { |n| "RT#{n}" }
    description { "Comfortable room with modern amenities" }
    base_price { 150.00 }
    max_occupancy { 4 }
    standard_occupancy { 2 }
    extra_person_charge { 25.00 }
    size_sqm { 30.5 }
    
    trait :suite do
      name { "Suite" }
      code { "STE" }
      base_price { 350.00 }
      max_occupancy { 6 }
      standard_occupancy { 4 }
      size_sqm { 65.0 }
    end
    
    trait :economy do
      name { "Economy" }
      code { "ECO" }
      base_price { 75.00 }
      max_occupancy { 2 }
      standard_occupancy { 1 }
      extra_person_charge { 15.00 }
      size_sqm { 20.0 }
    end
  end
end
