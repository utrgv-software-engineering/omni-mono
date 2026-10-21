# == Schema Information
#
# Table name: hotel_amenities
#
#  id              :bigint           not null, primary key
#  additional_cost :decimal(10, 2)
#  is_available    :boolean          default(TRUE), not null
#  is_free         :boolean          default(TRUE), not null
#  location        :string
#  notes           :text
#  operating_hours :string
#  created_at      :datetime         not null
#  updated_at      :datetime         not null
#  account_id      :bigint           not null
#  amenity_id      :bigint           not null
#  hotel_id        :bigint           not null
#
# Indexes
#
#  idx_on_account_id_hotel_id_is_available_6f4a4fce28              (account_id,hotel_id,is_available)
#  index_hotel_amenities_on_account_id                             (account_id)
#  index_hotel_amenities_on_amenity_id                             (amenity_id)
#  index_hotel_amenities_on_hotel_id                               (hotel_id)
#  index_hotel_amenities_on_hotel_id_and_amenity_id                (hotel_id,amenity_id) UNIQUE
#  index_hotel_amenities_on_hotel_id_and_is_available_and_is_free  (hotel_id,is_available,is_free)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (amenity_id => amenities.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
FactoryBot.define do
  factory :hotel_amenity do
    association :account
    association :hotel
    association :amenity
    is_available { true }
    is_free { true }
    additional_cost { nil }
    location { nil }
    operating_hours { nil }
    notes { nil }
    
    trait :unavailable do
      is_available { false }
    end
    
    trait :paid do
      is_free { false }
      additional_cost { 25.00 }
    end
    
    trait :with_location do
      location { "Lobby Level" }
    end
    
    trait :with_hours do
      operating_hours { "6:00 AM - 10:00 PM" }
    end
    
    trait :twenty_four_seven do
      operating_hours { "24/7" }
    end
    
    # Common hotel amenity configurations
    trait :pool do
      association :amenity, factory: [:amenity, :pool]
      location { "Rooftop" }
      operating_hours { "6:00 AM - 10:00 PM" }
      notes { "Heated pool, towels provided" }
    end
    
    trait :gym do
      association :amenity, factory: [:amenity, :gym]
      location { "2nd Floor" }
      operating_hours { "24/7" }
      notes { "Key card access required" }
    end
    
    trait :parking do
      association :amenity, factory: [:amenity, :parking]
      location { "Underground Garage" }
      operating_hours { "24/7" }
      notes { "Valet service available" }
    end
    
    trait :restaurant do
      association :amenity, factory: [:amenity, :restaurant]
      location { "Lobby Level" }
      operating_hours { "6:30 AM - 11:00 PM" }
      notes { "Reservations recommended for dinner" }
    end
    
    trait :business_center do
      association :amenity, factory: [:amenity, :business_center]
      location { "Mezzanine Level" }
      operating_hours { "24/7" }
      notes { "Printing and fax services available" }
    end
    
    # Paid amenity examples
    trait :spa do
      is_free { false }
      additional_cost { 150.00 }
      location { "3rd Floor" }
      operating_hours { "9:00 AM - 9:00 PM" }
      notes { "Advance booking required" }
    end
    
    trait :pet_friendly do
      is_free { false }
      additional_cost { 50.00 }
      notes { "Maximum 2 pets, under 50 lbs, per night charge" }
    end
  end
end
