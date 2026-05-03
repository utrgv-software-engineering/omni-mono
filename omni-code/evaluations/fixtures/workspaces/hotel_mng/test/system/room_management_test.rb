# frozen_string_literal: true

require "application_system_test_case"

class RoomManagementTest < ApplicationSystemTestCase
  setup do
    @context = sign_in_as_role(:hotel_manager)
    @hotel = @context[:hotel]
    @account = @context[:account]
    @room_type = create(:room_type, hotel: @hotel, account: @account, name: "Standard Room")
  end

  test "manager can create a room type" do
    visit new_hotel_room_type_path(@hotel)

    fill_in "room_type_name", with: "Deluxe Suite"
    fill_in "room_type_code", with: "DLXS"
    fill_in "room_type_base_price", with: "299.00"
    fill_in "room_type_max_occupancy", with: 4
    fill_in "room_type_standard_occupancy", with: 2

    click_button "Create Room type"

    assert_text "Room type was successfully created"
    assert_text "Deluxe Suite"
  end

  test "manager can create a room" do
    visit new_hotel_room_path(@hotel)

    fill_in "room_room_number", with: "401"
    select @room_type.name, from: "room_room_type_id"
    fill_in "room_floor", with: 4

    click_button "Create Room"

    assert_text "Room was successfully created"
    assert_text "401"
  end

  test "staff can view room status" do
    room = create(:room, hotel: @hotel, room_type: @room_type, account: @account, room_number: "101", status: "available")

    visit hotel_room_path(@hotel, room)

    assert_text "101"
    # Page displays "Available" with capital A
    assert_text "Available"
    assert_text @room_type.name
  end
end
