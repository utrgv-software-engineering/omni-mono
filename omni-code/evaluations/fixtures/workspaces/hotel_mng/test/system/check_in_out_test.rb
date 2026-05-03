# frozen_string_literal: true

require "application_system_test_case"

class CheckInOutTest < ApplicationSystemTestCase
  setup do
    @context = sign_in_as_role(:front_desk)
    @hotel = @context[:hotel]
    @account = @context[:account]
    @room_type = create(:room_type, hotel: @hotel, account: @account, name: "Standard Room")
    @room = create(:room, hotel: @hotel, room_type: @room_type, status: "available", account: @account, room_number: "101")
    @guest = create(:guest, account: @account, first_name: "John", last_name: "Doe")
    @reservation = create(:reservation,
      account: @account,
      hotel: @hotel,
      guest: @guest,
      room_type: @room_type,
      status: "confirmed",
      check_in_date: Date.current,
      check_out_date: Date.current + 3.days
    )
  end

  test "front desk can check in a guest" do
    visit reservation_path(@reservation)
    click_link "Check In"

    # Select room from dropdown (the option text includes "Room 101")
    select "Room 101", from: "room_id"

    # Check the ID verified checkbox - it's a required checkbox with type="checkbox" and name="id_verified"
    check "id_verified"

    click_button "Complete Check-In"

    # After successful check-in, should show success message and status
    assert_text "Guest successfully checked in"
  end

  test "front desk can check out a guest" do
    # Set up checked-in reservation
    @reservation.update!(status: "checked_in", room: @room, actual_check_in: Time.current)
    @room.update!(status: "occupied")

    visit reservation_path(@reservation)
    # The button text in the view is "Check Out" with a space
    click_link "Check Out"

    click_button "Complete Check-Out"

    # After successful check-out, should show success message and status
    assert_text "Guest successfully checked out"
  end
end
