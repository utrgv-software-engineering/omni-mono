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
require "test_helper"

class RoomTypeTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account,
                       base_price: 100, standard_occupancy: 2, 
                       max_occupancy: 4, extra_person_charge: 25)
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "calculates price for standard occupancy" do
    price = @room_type.calculate_price(nights: 3, guests: 2)
    assert_equal 300, price # 100 * 3 nights, no extra charge
  end
  
  test "calculates price with extra guests" do
    price = @room_type.calculate_price(nights: 3, guests: 3)
    # Base: 100 * 3 = 300
    # Extra guest: 1 * 25 * 3 = 75
    # Total: 375
    assert_equal 375, price
  end
  
  test "calculates price at maximum occupancy" do
    price = @room_type.calculate_price(nights: 2, guests: 4)
    # Base: 100 * 2 = 200
    # Extra guests: 2 * 25 * 2 = 100
    # Total: 300
    assert_equal 300, price
  end
  
  test "does not charge for guests below standard occupancy" do
    price = @room_type.calculate_price(nights: 2, guests: 1)
    assert_equal 200, price # 100 * 2 nights, no reduction
  end
  
  test "counts available rooms for date range" do
    # Create 3 rooms of this type
    room1 = create(:room, room_type: @room_type, hotel: @hotel, account: @account)
    room2 = create(:room, room_type: @room_type, hotel: @hotel, account: @account)
    room3 = create(:room, :maintenance, room_type: @room_type, hotel: @hotel, account: @account)
    
    # Book one room
    create(:reservation, :confirmed, room: room1, room_type: @room_type, 
           hotel: @hotel, account: @account,
           check_in_date: Date.tomorrow, check_out_date: 3.days.from_now)
    
    count = @room_type.available_rooms_count(Date.tomorrow, 3.days.from_now)
    assert_equal 1, count # Only room2 is available
  end
  
  test "validates standard occupancy within max occupancy" do
    room_type = build(:room_type, hotel: @hotel, account: @account,
                      standard_occupancy: 5, max_occupancy: 4)
    
    assert_not room_type.valid?
    assert_includes room_type.errors[:standard_occupancy], 
                   "cannot be greater than maximum occupancy"
  end
end
