# frozen_string_literal: true

require "application_system_test_case"

class HousekeepingTest < ApplicationSystemTestCase
  test "housekeeping can update room status to available" do
    context = sign_in_as_role(:housekeeping)
    hotel = context[:hotel]
    account = context[:account]
    room_type = create(:room_type, hotel: hotel, account: account, name: "Standard Room")
    room = create(:room, hotel: hotel, room_type: room_type, account: account, status: "cleaning", room_number: "101")

    visit hotel_room_path(hotel, room)

    # The room show page has Quick Status Update buttons (submit inputs), not a dropdown
    # When room is in "cleaning" status, housekeeping can click "Set Available" button
    click_button "Set Available"

    assert_text "Room was successfully updated"
  end

  test "housekeeping cannot access reservations page" do
    context = sign_in_as_role(:housekeeping)

    visit reservations_path

    # Housekeeping role should see "not authorized" message from Pundit
    assert_text "not authorized"
  end
end
