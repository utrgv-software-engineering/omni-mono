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
require "test_helper"

class AmenityTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @amenity = create(:amenity, account: @account, name: "Test Amenity")
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "hotel_amenity? returns true for hotel-level categories" do
    hotel_amenity = create(:amenity, account: @account, category: "hotel")
    outdoor_amenity = create(:amenity, account: @account, category: "outdoor")
    wellness_amenity = create(:amenity, account: @account, category: "wellness")
    business_amenity = create(:amenity, account: @account, category: "business")
    
    assert hotel_amenity.hotel_amenity?
    assert outdoor_amenity.hotel_amenity?
    assert wellness_amenity.hotel_amenity?
    assert business_amenity.hotel_amenity?
  end
  
  test "hotel_amenity? returns false for room-level categories" do
    room_amenity = create(:amenity, account: @account, category: "room")
    bathroom_amenity = create(:amenity, account: @account, category: "bathroom")
    
    assert_not room_amenity.hotel_amenity?
    assert_not bathroom_amenity.hotel_amenity?
  end
  
  test "room_amenity? returns true for room-level categories" do
    room_amenity = create(:amenity, account: @account, category: "room")
    bathroom_amenity = create(:amenity, account: @account, category: "bathroom")
    kitchen_amenity = create(:amenity, account: @account, category: "kitchen")
    entertainment_amenity = create(:amenity, account: @account, category: "entertainment")
    
    assert room_amenity.room_amenity?
    assert bathroom_amenity.room_amenity?
    assert kitchen_amenity.room_amenity?
    assert entertainment_amenity.room_amenity?
  end
  
  test "room_amenity? returns false for hotel-level categories" do
    hotel_amenity = create(:amenity, account: @account, category: "hotel")
    outdoor_amenity = create(:amenity, account: @account, category: "outdoor")
    
    assert_not hotel_amenity.room_amenity?
    assert_not outdoor_amenity.room_amenity?
  end
  
  test "icon_class returns custom icon when present" do
    amenity = create(:amenity, account: @account, icon: "custom-icon")
    assert_equal "custom-icon", amenity.icon_class
  end
  
  test "icon_class returns mapped icon for known amenity names" do
    # Create new amenities with mapped names
    wifi_amenity = build(:amenity, account: @account, name: "WiFi", icon: nil)
    pool_amenity = build(:amenity, account: @account, name: "Pool", icon: nil)
    
    assert_equal "wifi", wifi_amenity.icon_class
    assert_equal "person-swimming", pool_amenity.icon_class
  end
  
  test "icon_class returns default icon for unknown amenity names" do
    unknown_amenity = create(:amenity, account: @account, name: "Unknown Amenity", icon: nil)
    assert_equal "circle", unknown_amenity.icon_class
  end
  
  test "display_name formats name with humanized category" do
    amenity = create(:amenity, account: @account, name: "Swimming Pool", category: "outdoor")
    assert_equal "Swimming Pool (Outdoor)", amenity.display_name
  end
  
  test "scopes filter amenities by category groupings" do
    # Create amenities in different categories
    hotel_amenity = create(:amenity, account: @account, category: "hotel")
    outdoor_amenity = create(:amenity, account: @account, category: "outdoor")
    room_amenity = create(:amenity, account: @account, category: "room")
    bathroom_amenity = create(:amenity, account: @account, category: "bathroom")
    accessibility_amenity = create(:amenity, account: @account, category: "accessibility")
    family_amenity = create(:amenity, account: @account, category: "family")
    
    # Test hotel_level scope
    hotel_level = Amenity.hotel_level
    assert_includes hotel_level, hotel_amenity
    assert_includes hotel_level, outdoor_amenity
    assert_not_includes hotel_level, room_amenity
    
    # Test room_level scope
    room_level = Amenity.room_level
    assert_includes room_level, room_amenity
    assert_includes room_level, bathroom_amenity
    assert_not_includes room_level, hotel_amenity
    
    # Test specific category scopes
    assert_includes Amenity.accessibility, accessibility_amenity
    assert_includes Amenity.family_friendly, family_amenity
  end
  
  test "countable scope filters countable amenities" do
    countable = create(:amenity, account: @account, is_countable: true)
    non_countable = create(:amenity, account: @account, is_countable: false)
    
    countable_amenities = Amenity.countable
    assert_includes countable_amenities, countable
    assert_not_includes countable_amenities, non_countable
  end
end
