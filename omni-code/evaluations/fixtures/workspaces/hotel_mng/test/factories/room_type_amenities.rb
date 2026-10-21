# == Schema Information
#
# Table name: room_type_amenities
#
#  id           :bigint           not null, primary key
#  quantity     :integer          default(1), not null
#  created_at   :datetime         not null
#  updated_at   :datetime         not null
#  account_id   :bigint           not null
#  amenity_id   :bigint           not null
#  room_type_id :bigint           not null
#
# Indexes
#
#  index_room_type_amenities_on_account_id                   (account_id)
#  index_room_type_amenities_on_account_id_and_room_type_id  (account_id,room_type_id)
#  index_room_type_amenities_on_amenity_id                   (amenity_id)
#  index_room_type_amenities_on_room_type_id                 (room_type_id)
#  index_room_type_amenities_on_room_type_id_and_amenity_id  (room_type_id,amenity_id) UNIQUE
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (amenity_id => amenities.id)
#  fk_rails_...  (room_type_id => room_types.id)
#
FactoryBot.define do
  factory :room_type_amenity do
    association :account
    association :room_type
    association :amenity
    quantity { 1 }
    
    trait :multiple do
      quantity { 2 }
    end
    
    # Common room amenity configurations
    trait :air_conditioning do
      association :amenity, factory: [:amenity, :air_conditioning]
      quantity { 1 }
    end
    
    trait :single_tv do
      association :amenity, factory: [:amenity, :tv]
      quantity { 1 }
    end
    
    trait :double_tv do
      association :amenity, factory: [:amenity, :tv]
      quantity { 2 }
    end
    
    trait :mini_bar do
      association :amenity, factory: [:amenity, :mini_bar]
      quantity { 1 }
    end
    
    trait :safe do
      association :amenity, factory: [:amenity, :safe]
      quantity { 1 }
    end
    
    trait :coffee_maker do
      association :amenity, factory: [:amenity, :coffee_maker]
      quantity { 1 }
    end
    
    # Bed configurations
    trait :single_queen_bed do
      association :amenity, factory: [:amenity, :queen_bed]
      quantity { 1 }
    end
    
    trait :double_queen_beds do
      association :amenity, factory: [:amenity, :queen_bed]
      quantity { 2 }
    end
    
    trait :single_king_bed do
      association :amenity, factory: [:amenity, :king_bed]
      quantity { 1 }
    end
    
    # Bathroom amenities
    trait :single_bathrobe do
      association :amenity, factory: [:amenity, :bathrobe]
      quantity { 1 }
    end
    
    trait :double_bathrobes do
      association :amenity, factory: [:amenity, :bathrobe]
      quantity { 2 }
    end
    
    trait :family_bathrobes do
      association :amenity, factory: [:amenity, :bathrobe]
      quantity { 4 }
    end
    
    # Standard room setup - basic amenities
    trait :standard_room_setup do
      after(:create) do |room_type_amenity|
        account = room_type_amenity.account
        room_type = room_type_amenity.room_type
        
        # Create a set of standard amenities if this is the first one
        if room_type.room_type_amenities.count == 1
          amenities_to_add = [
            { factory: [:amenity, :air_conditioning], quantity: 1 },
            { factory: [:amenity, :tv], quantity: 1 },
            { factory: [:amenity, :safe], quantity: 1 },
            { factory: [:amenity, :coffee_maker], quantity: 1 }
          ]
          
          amenities_to_add.each do |amenity_config|
            amenity = create(amenity_config[:factory].first, 
                           amenity_config[:factory].second, 
                           account: account)
            create(:room_type_amenity, 
                  account: account,
                  room_type: room_type,
                  amenity: amenity,
                  quantity: amenity_config[:quantity])
          end
        end
      end
    end
    
    # Deluxe room setup - premium amenities
    trait :deluxe_room_setup do
      after(:create) do |room_type_amenity|
        account = room_type_amenity.account
        room_type = room_type_amenity.room_type
        
        # Create a set of deluxe amenities if this is the first one
        if room_type.room_type_amenities.count == 1
          amenities_to_add = [
            { factory: [:amenity, :air_conditioning], quantity: 1 },
            { factory: [:amenity, :tv], quantity: 2 },
            { factory: [:amenity, :mini_bar], quantity: 1 },
            { factory: [:amenity, :safe], quantity: 1 },
            { factory: [:amenity, :coffee_maker], quantity: 1 },
            { factory: [:amenity, :bathrobe], quantity: 2 }
          ]
          
          amenities_to_add.each do |amenity_config|
            amenity = create(amenity_config[:factory].first,
                           amenity_config[:factory].second,
                           account: account)
            create(:room_type_amenity,
                  account: account,
                  room_type: room_type,
                  amenity: amenity,
                  quantity: amenity_config[:quantity])
          end
        end
      end
    end
  end
end
