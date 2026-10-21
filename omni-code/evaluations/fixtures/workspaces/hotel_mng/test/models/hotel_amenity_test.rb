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
require "test_helper"

class HotelAmenityTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account)
    @amenity = create(:amenity, :pool, account: @account)
    @hotel_amenity = create(:hotel_amenity, account: @account, hotel: @hotel, amenity: @amenity)
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "has_cost? returns false when free" do
    free_amenity = create(:hotel_amenity, account: @account, hotel: @hotel, 
                          is_free: true, additional_cost: nil)
    assert_not free_amenity.has_cost?
  end
  
  test "has_cost? returns true when not free with positive cost" do
    paid_amenity = create(:hotel_amenity, account: @account, hotel: @hotel,
                         is_free: false, additional_cost: 50.00)
    assert paid_amenity.has_cost?
  end
  
  test "has_cost? returns false when not free but no cost specified" do
    amenity = create(:hotel_amenity, account: @account, hotel: @hotel,
                    is_free: false, additional_cost: nil)
    assert_not amenity.has_cost?
  end
  
  test "display_price returns Free for free amenities" do
    free_amenity = create(:hotel_amenity, account: @account, hotel: @hotel, is_free: true)
    assert_equal "Free", free_amenity.display_price
  end
  
  test "display_price returns formatted price for paid amenities" do
    paid_amenity = create(:hotel_amenity, account: @account, hotel: @hotel,
                         is_free: false, additional_cost: 75.50)
    assert_equal "$75.5", paid_amenity.display_price
  end
  
  test "display_price returns Inquire when not free but cost is nil" do
    amenity = create(:hotel_amenity, account: @account, hotel: @hotel,
                    is_free: false, additional_cost: nil)
    assert_equal "Inquire", amenity.display_price
  end
  
  test "formatted_operating_hours returns 24/7 when blank" do
    amenity = create(:hotel_amenity, account: @account, hotel: @hotel, operating_hours: nil)
    assert_equal "24/7", amenity.formatted_operating_hours
    
    amenity2 = create(:hotel_amenity, account: @account, hotel: @hotel, operating_hours: "")
    assert_equal "24/7", amenity2.formatted_operating_hours
  end
  
  test "formatted_operating_hours returns actual hours when present" do
    amenity = create(:hotel_amenity, account: @account, hotel: @hotel,
                    operating_hours: "9:00 AM - 5:00 PM")
    assert_equal "9:00 AM - 5:00 PM", amenity.formatted_operating_hours
  end
  
  test "currently_open? returns true for 24/7 amenities" do
    always_open = create(:hotel_amenity, account: @account, hotel: @hotel, 
                         operating_hours: "24/7")
    assert always_open.currently_open?
  end
  
  test "currently_open? returns true when hours are blank" do
    no_hours = create(:hotel_amenity, account: @account, hotel: @hotel, 
                     operating_hours: nil)
    assert no_hours.currently_open?
  end
  
  test "sets default values on validation" do
    amenity = HotelAmenity.new(account: @account, hotel: @hotel, amenity: @amenity)
    amenity.valid?
    
    assert_equal true, amenity.is_free
    assert_equal true, amenity.is_available
  end
  
  test "scopes filter amenities correctly" do
    free_amenity = create(:hotel_amenity, account: @account, hotel: @hotel, is_free: true)
    paid_amenity = create(:hotel_amenity, :paid, account: @account, hotel: @hotel)
    unavailable_amenity = create(:hotel_amenity, :unavailable, account: @account, hotel: @hotel)
    
    # Test free scope
    assert_includes HotelAmenity.unscoped.free, free_amenity
    assert_not_includes HotelAmenity.unscoped.free, paid_amenity
    
    # Test paid scope
    assert_includes HotelAmenity.unscoped.paid, paid_amenity
    assert_not_includes HotelAmenity.unscoped.paid, free_amenity
    
    # Test available scope (part of default scope)
    assert_includes HotelAmenity.all, free_amenity
    assert_includes HotelAmenity.all, paid_amenity
    assert_not_includes HotelAmenity.all, unavailable_amenity
    
    # Test unavailable scope
    assert_includes HotelAmenity.unscoped.unavailable, unavailable_amenity
    assert_not_includes HotelAmenity.unscoped.unavailable, free_amenity
  end
end
