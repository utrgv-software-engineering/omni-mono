# == Schema Information
#
# Table name: reservations
#
#  id                  :bigint           not null, primary key
#  actual_check_in     :datetime
#  actual_check_out    :datetime
#  adults              :integer          default(1)
#  check_in_date       :date             not null
#  check_out_date      :date             not null
#  children            :integer          default(0)
#  confirmation_number :string           not null
#  currency            :string           default("USD")
#  infants             :integer          default(0)
#  internal_notes      :text
#  paid_amount         :decimal(10, 2)   default(0.0)
#  payment_status      :string           default("pending")
#  source              :string
#  special_requests    :text
#  status              :string           default("pending")
#  total_amount        :decimal(10, 2)   not null
#  created_at          :datetime         not null
#  updated_at          :datetime         not null
#  account_id          :bigint           not null
#  guest_id            :bigint           not null
#  hotel_id            :bigint           not null
#  room_id             :bigint
#  room_type_id        :bigint           not null
#
# Indexes
#
#  idx_on_account_id_hotel_id_check_in_date_7087d23249       (account_id,hotel_id,check_in_date)
#  index_reservations_on_account_id                          (account_id)
#  index_reservations_on_account_id_and_confirmation_number  (account_id,confirmation_number) UNIQUE
#  index_reservations_on_account_id_and_hotel_id_and_status  (account_id,hotel_id,status)
#  index_reservations_on_guest_id                            (guest_id)
#  index_reservations_on_hotel_id                            (hotel_id)
#  index_reservations_on_room_id                             (room_id)
#  index_reservations_on_room_type_id                        (room_type_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (guest_id => guests.id)
#  fk_rails_...  (hotel_id => hotels.id)
#  fk_rails_...  (room_id => rooms.id)
#  fk_rails_...  (room_type_id => room_types.id)
#
require "test_helper"

class ReservationTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account, max_occupancy: 4)
    @room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    @guest = create(:guest, account: @account)
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "generates confirmation number on create" do
    reservation = create(:reservation, hotel: @hotel, account: @account,
                        guest: @guest, room_type: @room_type)
    
    assert_not_nil reservation.confirmation_number
    assert_match /\A[A-Z0-9]{8}\z/, reservation.confirmation_number
  end
  
  test "calculates nights correctly" do
    reservation = create(:reservation, hotel: @hotel, account: @account,
                        guest: @guest, room_type: @room_type,
                        check_in_date: Date.parse("2025-01-01"), 
                        check_out_date: Date.parse("2025-01-06"))
    
    assert_equal 5, reservation.nights
  end
  
  test "calculates total guests" do
    reservation = create(:reservation, hotel: @hotel, account: @account,
                        guest: @guest, room_type: @room_type,
                        adults: 2, children: 1, infants: 1)
    
    assert_equal 4, reservation.total_guests
  end
  
  test "calculates balance due" do
    reservation = create(:reservation, hotel: @hotel, account: @account,
                        guest: @guest, room_type: @room_type,
                        total_amount: 500, paid_amount: 150)
    
    assert_equal 350, reservation.balance_due
  end
  
  test "determines if fully paid" do
    partial = create(:reservation, hotel: @hotel, account: @account,
                    guest: @guest, room_type: @room_type,
                    total_amount: 500, paid_amount: 150)
    paid = create(:reservation, hotel: @hotel, account: @account,
                 guest: @guest, room_type: @room_type,
                 total_amount: 500, paid_amount: 500)
    
    assert_not partial.fully_paid?
    assert paid.fully_paid?
  end
  
  test "check in workflow" do
    reservation = create(:reservation, :confirmed, hotel: @hotel, account: @account,
                        guest: @guest, room_type: @room_type,
                        check_in_date: Date.today)
    
    assert reservation.can_check_in?
    
    reservation.check_in!(room_id: @room.id)
    
    assert_equal "checked_in", reservation.status
    assert_equal @room.id, reservation.room_id
    assert_not_nil reservation.actual_check_in
    
    @room.reload
    assert_equal "occupied", @room.status
  end
  
  test "check out workflow" do
    reservation = create(:reservation, :checked_in, hotel: @hotel, account: @account,
                        guest: @guest, room: @room, room_type: @room_type)
    @room.update!(status: "occupied")
    
    assert reservation.can_check_out?
    
    reservation.check_out!
    
    assert_equal "checked_out", reservation.status
    assert_not_nil reservation.actual_check_out
    
    @room.reload
    assert_equal "cleaning", @room.status
  end
  
  test "cancel workflow" do
    reservation = create(:reservation, :checked_in, hotel: @hotel, account: @account,
                        guest: @guest, room: @room, room_type: @room_type)
    @room.update!(status: "occupied")
    
    reservation.cancel!
    
    assert_equal "cancelled", reservation.status
    
    @room.reload
    assert_equal "available", @room.status
  end
  
  test "no show workflow" do
    reservation = create(:reservation, :confirmed, hotel: @hotel, account: @account,
                        guest: @guest, room: @room, room_type: @room_type)
    
    reservation.no_show!
    
    assert_equal "no_show", reservation.status
    
    @room.reload
    assert_equal "available", @room.status
  end
  
  test "validates check out after check in" do
    reservation = build(:reservation, hotel: @hotel, account: @account,
                       guest: @guest, room_type: @room_type,
                       check_in_date: Date.today, check_out_date: Date.yesterday)
    
    assert_not reservation.valid?
    assert_includes reservation.errors[:check_out_date], "must be after check-in date"
  end
  
  test "validates room availability for dates" do
    # Create existing reservation
    existing = create(:reservation, :confirmed, hotel: @hotel, account: @account,
                     guest: @guest, room: @room, room_type: @room_type,
                     check_in_date: Date.tomorrow, check_out_date: 5.days.from_now)
    
    # Try to create overlapping reservation
    overlapping = build(:reservation, hotel: @hotel, account: @account,
                       guest: create(:guest, account: @account), room: @room, room_type: @room_type,
                       check_in_date: 2.days.from_now, check_out_date: 4.days.from_now)
    
    assert_not overlapping.valid?
    assert_includes overlapping.errors[:room], "is not available for the selected dates"
  end
  
  test "allows overlapping reservation when cancelled" do
    # Create cancelled reservation
    cancelled = create(:reservation, :cancelled, hotel: @hotel, account: @account,
                      guest: @guest, room: @room, room_type: @room_type,
                      check_in_date: Date.tomorrow, check_out_date: 5.days.from_now)
    
    # Should allow new reservation for same dates
    new_reservation = build(:reservation, hotel: @hotel, account: @account,
                          guest: create(:guest, account: @account), room: @room, room_type: @room_type,
                          check_in_date: Date.tomorrow, check_out_date: 3.days.from_now)
    
    assert new_reservation.valid?
  end
  
  test "validates total occupancy within room capacity" do
    reservation = build(:reservation, hotel: @hotel, account: @account,
                       guest: @guest, room_type: @room_type,
                       adults: 3, children: 2) # Total: 5, max: 4
    
    assert_not reservation.valid?
    assert_includes reservation.errors[:base], "Total guests exceed room capacity of 4"
  end
  
  test "assigns room if available" do
    reservation = create(:reservation, hotel: @hotel, account: @account,
                        guest: @guest, room_type: @room_type)
    
    result = reservation.assign_room(@room)
    
    assert result
    assert_equal @room, reservation.room
  end
  
  test "does not assign room if not available" do
    # Block the room
    create(:reservation, :confirmed, hotel: @hotel, account: @account,
           guest: create(:guest, account: @account), room: @room, room_type: @room_type,
           check_in_date: Date.tomorrow, check_out_date: 5.days.from_now)
    
    reservation = create(:reservation, hotel: @hotel, account: @account,
                        guest: @guest, room_type: @room_type,
                        check_in_date: 2.days.from_now, check_out_date: 4.days.from_now)
    
    result = reservation.assign_room(@room)
    
    assert_not result
    assert_nil reservation.room
  end
  
  test "calculates revenue per night" do
    reservation = create(:reservation, hotel: @hotel, account: @account,
                        guest: @guest, room_type: @room_type,
                        total_amount: 450, check_in_date: Date.parse("2025-01-01"),
                        check_out_date: Date.parse("2025-01-04"))
    
    assert_in_delta 150.0, reservation.revenue_per_night, 0.01
  end
  
  test "room status updates on state changes" do
    reservation = create(:reservation, :confirmed, hotel: @hotel, account: @account,
                        guest: @guest, room: @room, room_type: @room_type)
    
    # Check in
    reservation.update!(status: "checked_in")
    @room.reload
    assert_equal "occupied", @room.status
    
    # Check out
    reservation.update!(status: "checked_out")
    @room.reload
    assert_equal "cleaning", @room.status
  end
end
