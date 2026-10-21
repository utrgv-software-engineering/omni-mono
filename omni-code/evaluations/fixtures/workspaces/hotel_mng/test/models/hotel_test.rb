# == Schema Information
#
# Table name: hotels
#
#  id             :bigint           not null, primary key
#  address        :string           default(""), not null
#  check_in_time  :time             default(2000-01-01 15:00:00.000000000 UTC +00:00)
#  check_out_time :time             default(2000-01-01 11:00:00.000000000 UTC +00:00)
#  city           :string           default(""), not null
#  country        :string           default(""), not null
#  description    :text             default("")
#  email          :string           default(""), not null
#  name           :string
#  phone          :string           default(""), not null
#  postal_code    :string           default(""), not null
#  star_rating    :decimal(2, 1)
#  state          :string           default(""), not null
#  subdomain      :string
#  timezone       :string           default("UTC"), not null
#  total_rooms    :integer          default(0)
#  website        :string           default("")
#  created_at     :datetime         not null
#  updated_at     :datetime         not null
#  account_id     :bigint           not null
#
# Indexes
#
#  index_hotels_on_account_id                (account_id)
#  index_hotels_on_account_id_and_subdomain  (account_id,subdomain) UNIQUE
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#
require "test_helper"

class HotelTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account, total_rooms: 50)
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "calculates occupancy rate correctly" do
    # Create some rooms and reservations
    room1 = create(:room, hotel: @hotel, account: @account)
    room2 = create(:room, hotel: @hotel, account: @account)
    room3 = create(:room, hotel: @hotel, account: @account)
    
    # Create checked-in reservations for today
    create(:reservation, :checked_in, hotel: @hotel, account: @account, 
           room: room1, check_in_date: Date.current, check_out_date: Date.tomorrow)
    create(:reservation, :checked_in, hotel: @hotel, account: @account,
           room: room2, check_in_date: Date.yesterday, check_out_date: Date.tomorrow)
    
    # Should have 2 occupied rooms out of 50 total
    assert_equal 4.0, @hotel.occupancy_rate
  end
  
  test "returns zero occupancy when no total_rooms set" do
    hotel = create(:hotel, account: @account, total_rooms: nil)
    assert_equal 0, hotel.occupancy_rate
  end
  
  test "returns zero occupancy when total_rooms is zero" do
    hotel = build(:hotel, account: @account, total_rooms: 0)
    # Skip validation to test the method behavior
    hotel.save(validate: false)
    assert_equal 0, hotel.occupancy_rate
  end
  
  test "finds available rooms for date range" do
    room_type = create(:room_type, hotel: @hotel, account: @account)
    available_room = create(:room, hotel: @hotel, account: @account, room_type: room_type)
    occupied_room = create(:room, hotel: @hotel, account: @account, room_type: room_type)
    maintenance_room = create(:room, :maintenance, hotel: @hotel, account: @account, room_type: room_type)
    
    # Create a reservation that occupies one room
    create(:reservation, :confirmed, hotel: @hotel, account: @account,
           room: occupied_room, check_in_date: Date.tomorrow, check_out_date: 5.days.from_now)
    
    available = @hotel.available_rooms(Date.tomorrow, 3.days.from_now)
    
    assert_includes available, available_room
    assert_not_includes available, occupied_room
    assert_not_includes available, maintenance_room
  end
  
  test "excludes rooms with overlapping reservations" do
    room = create(:room, hotel: @hotel, account: @account)
    
    # Create reservation that overlaps with search dates
    create(:reservation, :confirmed, hotel: @hotel, account: @account,
           room: room, check_in_date: Date.today, check_out_date: 5.days.from_now)
    
    available = @hotel.available_rooms(Date.tomorrow, 3.days.from_now)
    assert_not_includes available, room
  end
  
  test "includes rooms when reservation is cancelled" do
    room = create(:room, hotel: @hotel, account: @account)
    
    # Create cancelled reservation
    create(:reservation, :cancelled, hotel: @hotel, account: @account,
           room: room, check_in_date: Date.tomorrow, check_out_date: 3.days.from_now)
    
    available = @hotel.available_rooms(Date.tomorrow, 3.days.from_now)
    assert_includes available, room
  end
end
