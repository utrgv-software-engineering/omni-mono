# == Schema Information
#
# Table name: rooms
#
#  id              :bigint           not null, primary key
#  floor           :string
#  is_smoking      :boolean          default(FALSE)
#  last_cleaned_at :datetime
#  notes           :text
#  room_number     :string           not null
#  status          :string           default("available")
#  created_at      :datetime         not null
#  updated_at      :datetime         not null
#  account_id      :bigint           not null
#  hotel_id        :bigint           not null
#  room_type_id    :bigint           not null
#
# Indexes
#
#  index_rooms_on_account_id                          (account_id)
#  index_rooms_on_account_id_and_hotel_id_and_status  (account_id,hotel_id,status)
#  index_rooms_on_hotel_id                            (hotel_id)
#  index_rooms_on_hotel_id_and_room_number            (hotel_id,room_number) UNIQUE
#  index_rooms_on_room_type_id                        (room_type_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#  fk_rails_...  (room_type_id => room_types.id)
#
FactoryBot.define do
  factory :room do
    association :account
    association :hotel
    association :room_type
    sequence(:room_number) { |n| "#{100 + n}" }
    floor { "1" }
    status { "available" }
    is_smoking { false }
    notes { nil }
    last_cleaned_at { 1.hour.ago }
    
    trait :occupied do
      status { "occupied" }
    end
    
    trait :maintenance do
      status { "maintenance" }
      notes { "Under maintenance" }
    end
    
    trait :cleaning do
      status { "cleaning" }
      last_cleaned_at { 25.hours.ago }
    end
    
    trait :smoking do
      is_smoking { true }
    end
  end
end
