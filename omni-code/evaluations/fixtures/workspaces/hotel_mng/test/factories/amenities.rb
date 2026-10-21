# == Schema Information
#
# Table name: amenities
#
#  id           :bigint           not null, primary key
#  category     :string           not null
#  description  :text
#  icon         :string
#  is_countable :boolean          default(FALSE), not null
#  name         :string           not null
#  created_at   :datetime         not null
#  updated_at   :datetime         not null
#  account_id   :bigint           not null
#
# Indexes
#
#  index_amenities_on_account_id               (account_id)
#  index_amenities_on_account_id_and_category  (account_id,category)
#  index_amenities_on_account_id_and_name      (account_id,name) UNIQUE
#  index_amenities_on_category                 (category)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#
FactoryBot.define do
  factory :amenity do
    association :account
    sequence(:name) { |n| "Amenity #{n}" }
    category { "hotel" }
    icon { "circle" }
    description { "A wonderful amenity for guests" }
    is_countable { false }
    
    # Hotel amenities
    trait :wifi do
      name { "Free WiFi" }
      category { "hotel" }
      icon { "wifi" }
      description { "High-speed wireless internet throughout the property" }
    end
    
    trait :pool do
      name { "Swimming Pool" }
      category { "outdoor" }
      icon { "person-swimming" }
      description { "Outdoor heated swimming pool" }
    end
    
    trait :gym do
      name { "Fitness Center" }
      category { "wellness" }
      icon { "dumbbell" }
      description { "24/7 gym with modern equipment" }
    end
    
    trait :parking do
      name { "Free Parking" }
      category { "hotel" }
      icon { "square-parking" }
      description { "Complimentary self-parking for guests" }
    end
    
    trait :restaurant do
      name { "Restaurant" }
      category { "hotel" }
      icon { "utensils" }
      description { "On-site dining serving breakfast, lunch, and dinner" }
    end
    
    # Room amenities
    trait :air_conditioning do
      name { "Air Conditioning" }
      category { "room" }
      icon { "snowflake" }
      description { "Individual climate control" }
    end
    
    trait :tv do
      name { "Flat Screen TV" }
      category { "entertainment" }
      icon { "tv" }
      description { "Smart TV with cable channels" }
      is_countable { true }
    end
    
    trait :mini_bar do
      name { "Mini Bar" }
      category { "room" }
      icon { "wine-bottle" }
      description { "Stocked mini refrigerator" }
    end
    
    trait :safe do
      name { "Safe" }
      category { "room" }
      icon { "vault" }
      description { "In-room electronic safe" }
    end
    
    trait :coffee_maker do
      name { "Coffee Maker" }
      category { "kitchen" }
      icon { "mug-hot" }
      description { "Coffee and tea making facilities" }
    end
    
    # Countable room amenities
    trait :queen_bed do
      name { "Queen Bed" }
      category { "room" }
      icon { "bed" }
      description { "Queen size bed with premium mattress" }
      is_countable { true }
    end
    
    trait :king_bed do
      name { "King Bed" }
      category { "room" }
      icon { "bed" }
      description { "King size bed with premium mattress" }
      is_countable { true }
    end
    
    trait :bathrobe do
      name { "Bathrobe" }
      category { "bathroom" }
      icon { "vest" }
      description { "Plush terry bathrobes" }
      is_countable { true }
    end
    
    # Accessibility
    trait :wheelchair_accessible do
      name { "Wheelchair Accessible" }
      category { "accessibility" }
      icon { "wheelchair" }
      description { "Full wheelchair accessibility" }
    end
    
    # Business
    trait :business_center do
      name { "Business Center" }
      category { "business" }
      icon { "briefcase" }
      description { "Computer workstations and printing services" }
    end
  end
end
