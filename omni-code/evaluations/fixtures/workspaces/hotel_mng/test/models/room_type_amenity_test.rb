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
require "test_helper"

class RoomTypeAmenityTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @amenity = create(:amenity, account: @account, name: "TV", is_countable: true)
    @room_type_amenity = create(:room_type_amenity, account: @account, 
                               room_type: @room_type, amenity: @amenity, quantity: 2)
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "display_text shows single item without pluralization" do
    tv_amenity = create(:amenity, account: @account, name: "Television", is_countable: true)
    single_amenity = create(:room_type_amenity, account: @account, room_type: @room_type,
                           amenity: tv_amenity, quantity: 1)
    assert_equal "Television", single_amenity.display_text
  end
  
  test "display_text pluralizes countable amenities with quantity > 1" do
    assert_equal "2 TVs", @room_type_amenity.display_text
  end
  
  test "display_text shows name only for non-countable amenities regardless of quantity" do
    non_countable = create(:amenity, account: @account, name: "WiFi", is_countable: false)
    amenity = create(:room_type_amenity, account: @account, room_type: @room_type,
                    amenity: non_countable, quantity: 5)
    assert_equal "WiFi", amenity.display_text
  end
  
  test "to_display_hash returns complete amenity information" do
    amenity = create(:amenity, account: @account, name: "Mini Bar", 
                    category: "room", icon: "wine-bottle", 
                    description: "Fully stocked", is_countable: false)
    room_amenity = create(:room_type_amenity, account: @account, room_type: @room_type,
                         amenity: amenity, quantity: 1)
    
    hash = room_amenity.to_display_hash
    
    assert_equal "Mini Bar", hash[:name]
    assert_equal "room", hash[:category]
    assert_equal "wine-bottle", hash[:icon]
    assert_equal 1, hash[:quantity]
    assert_equal "Mini Bar", hash[:display_text]
    assert_equal "Fully stocked", hash[:description]
  end
  
  test "delegates amenity attributes correctly" do
    amenity = create(:amenity, account: @account, 
                    name: "Safe", category: "room", 
                    icon: "vault", description: "Electronic safe")
    room_amenity = create(:room_type_amenity, account: @account, 
                         room_type: @room_type, amenity: amenity)
    
    assert_equal "Safe", room_amenity.amenity_name
    assert_equal "room", room_amenity.amenity_category
    assert_equal "vault", room_amenity.amenity_icon
    assert_equal "Electronic safe", room_amenity.amenity_description
  end
  
  test "sets default quantity to 1" do
    amenity = RoomTypeAmenity.new(account: @account, room_type: @room_type, amenity: @amenity)
    amenity.valid?
    
    assert_equal 1, amenity.quantity
  end
  
  test "scopes filter by countable status" do
    countable_amenity = create(:amenity, account: @account, is_countable: true)
    non_countable_amenity = create(:amenity, account: @account, is_countable: false)
    
    countable_rta = create(:room_type_amenity, account: @account, 
                          room_type: @room_type, amenity: countable_amenity)
    non_countable_rta = create(:room_type_amenity, account: @account,
                              room_type: @room_type, amenity: non_countable_amenity)
    
    # Test countable scope
    countable_items = RoomTypeAmenity.countable
    assert_includes countable_items, countable_rta
    assert_not_includes countable_items, non_countable_rta
    
    # Test non_countable scope
    non_countable_items = RoomTypeAmenity.non_countable
    assert_includes non_countable_items, non_countable_rta
    assert_not_includes non_countable_items, countable_rta
  end
  
  test "with_amenity_details scope includes amenity association" do
    # Create a new room type amenity
    rta = create(:room_type_amenity, account: @account, room_type: @room_type)
    
    # Without includes, accessing amenity would trigger additional query
    # With includes, it should be preloaded
    loaded_rta = RoomTypeAmenity.with_amenity_details.find(rta.id)
    
    # Check that amenity association is loaded
    assert loaded_rta.association(:amenity).loaded?
  end
end
