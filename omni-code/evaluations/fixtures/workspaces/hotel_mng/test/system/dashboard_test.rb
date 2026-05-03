# frozen_string_literal: true

require "application_system_test_case"

class DashboardTest < ApplicationSystemTestCase
  test "staff can view dashboard with metrics" do
    context = sign_in_as_role(:front_desk)
    hotel = context[:hotel]
    account = context[:account]
    room_type = create(:room_type, hotel: hotel, account: account)
    guest = create(:guest, account: account)

    # Create some reservations for metrics (ensure check_out_date > check_in_date)
    create(:reservation,
      hotel: hotel,
      account: account,
      guest: guest,
      room_type: room_type,
      status: "confirmed",
      check_in_date: Date.current,
      check_out_date: Date.current + 2.days
    )
    create(:reservation,
      hotel: hotel,
      account: account,
      guest: guest,
      room_type: room_type,
      status: "checked_in",
      check_in_date: Date.current - 1.day,
      check_out_date: Date.current
    )

    visit authenticated_root_path

    # Verify key dashboard elements
    assert_text "Today's Arrivals"
    assert_text "Today's Departures"
    assert_text "Occupancy Rate"
    assert_text "Recent Reservations"
    assert_text "Room Status"
    assert_text "Quick Actions"
  end
end
