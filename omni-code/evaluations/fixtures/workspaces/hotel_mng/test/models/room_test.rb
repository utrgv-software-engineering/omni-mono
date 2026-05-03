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
require "test_helper"

class RoomTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "checks availability for dates without reservations" do
    assert @room.available_for_dates?(Date.tomorrow, 3.days.from_now)
  end
  
  test "not available for dates with overlapping reservation" do
    create(:reservation, :confirmed, room: @room, hotel: @hotel, account: @account,
           check_in_date: Date.tomorrow, check_out_date: 5.days.from_now)
    
    assert_not @room.available_for_dates?(Date.tomorrow, 3.days.from_now)
  end
  
  test "available when reservation is cancelled" do
    create(:reservation, :cancelled, room: @room, hotel: @hotel, account: @account,
           check_in_date: Date.tomorrow, check_out_date: 3.days.from_now)
    
    assert @room.available_for_dates?(Date.tomorrow, 3.days.from_now)
  end
  
  test "not available when room is under maintenance" do
    @room.update!(status: "maintenance")
    assert_not @room.available_for_dates?(Date.tomorrow, 3.days.from_now)
  end
  
  test "finds current reservation for checked-in guest" do
    reservation = create(:reservation, :checked_in, room: @room, hotel: @hotel, account: @account,
                        check_in_date: Date.yesterday, check_out_date: Date.tomorrow)
    
    assert_equal reservation, @room.current_reservation
  end
  
  test "returns nil when no current reservation" do
    assert_nil @room.current_reservation
  end
  
  test "mark_as_cleaned updates status and timestamp" do
    @room.update!(status: "cleaning")
    time_before = Time.current
    
    @room.mark_as_cleaned!
    
    assert_equal "available", @room.status
    assert @room.last_cleaned_at >= time_before
  end
  
  test "needs_cleaning when status is cleaning" do
    @room.update!(status: "cleaning")
    assert @room.needs_cleaning?
  end
  
  test "needs_cleaning when last cleaned over 24 hours ago" do
    @room.update!(last_cleaned_at: 25.hours.ago)
    assert @room.needs_cleaning?
  end
  
  test "does not need cleaning when recently cleaned" do
    @room.update!(last_cleaned_at: 1.hour.ago, status: "available")
    assert_not @room.needs_cleaning?
  end
  
  test "status query methods" do
    @room.update!(status: "available")
    assert @room.available?
    assert_not @room.occupied?
    assert_not @room.under_maintenance?
    assert_not @room.being_cleaned?
    
    @room.update!(status: "occupied")
    assert @room.occupied?
    assert_not @room.available?
    
    @room.update!(status: "maintenance")
    assert @room.under_maintenance?
    
    @room.update!(status: "cleaning")
    assert @room.being_cleaned?
  end
end
